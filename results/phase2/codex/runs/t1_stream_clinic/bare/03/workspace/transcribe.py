#!/usr/bin/env python3
"""Paced PCM streaming with provider-side redaction and a lossless event log."""

import argparse
import asyncio
import contextlib
import json
import os
from pathlib import Path
import sys
import tempfile
from urllib.parse import urlencode
import wave

from websockets.asyncio.client import connect


# Keep clinical vocabulary (drug, medical_condition, medical_process) readable.
PII_POLICIES = [
    "person_name", "phone_number", "us_social_security_number", "date_of_birth",
    "email_address", "location", "healthcare_number", "account_number",
    "banking_information", "credit_card_number", "credit_card_expiration",
    "credit_card_cvv", "drivers_license", "passport_number", "password",
    "ip_address", "url", "username", "vehicle_id", "person_age",
]


class TranscriptionError(Exception):
    pass


def parameters():
    return {
        "sample_rate": 16000,
        "encoding": "pcm_s16le",
        "speech_model": "u3-rt-pro",
        "mode": "max_accuracy",
        "domain": "medical-v1",
        "speaker_labels": "true",
        "redact_pii": "true",
        "redact_pii_policies": json.dumps(PII_POLICIES),
        "redact_pii_sub": "entity_name",
        "include_partial_turns": "false",
        "format_turns": "true",
        "customer_support_audio_capture": "false",
    }


class Turns:
    def __init__(self):
        self.finals = {}
        self.speakers = {}

    def accept(self, event):
        if event.get("type") == "SpeakerRevision":
            for item in event.get("revisions", []):
                if item.get("speaker_label") is not None:
                    self.speakers[item["turn_order"]] = item["speaker_label"]
        elif event.get("type") == "Turn" and event.get("end_of_turn"):
            order = event["turn_order"]
            previous = self.finals.get(order)
            # Formatting arrives as a replacement for the same turn.
            if previous and previous.get("turn_is_formatted") and not event.get("turn_is_formatted"):
                return
            self.finals[order] = event
            if event.get("speaker_label") is not None:
                self.speakers[order] = event["speaker_label"]

    def result(self):
        return {"turns": [
            {"speaker": str(self.speakers.get(order, "Unknown")),
             "text": event["transcript"]}
            for order, event in sorted(self.finals.items())
            if event.get("transcript", "").strip()
        ]}


def check_begin(event):
    config = event.get("configuration", {})
    if (config.get("redact_pii") is not True
            or config.get("speaker_labels") is not True
            or config.get("domain") != "medical-v1"):
        raise TranscriptionError(
            "AssemblyAI did not confirm redaction, speaker labels, and medical domain; no audio sent."
        )


async def receive(ws, audit, turns, begun, terminated):
    async for raw in ws:
        # Preserve the application message exactly; do not round-trip through JSON.
        payload = raw.encode("utf-8") if isinstance(raw, str) else raw
        audit.write(payload + b"\n")
        audit.flush()
        event = json.loads(payload)
        if not isinstance(event, dict):
            raise TranscriptionError("AssemblyAI returned a non-object message.")
        kind = event.get("type")
        if kind == "Error" or "error" in event:
            raise TranscriptionError("AssemblyAI returned an error; see the frames log.")
        if kind == "Warning":
            raise TranscriptionError("AssemblyAI returned a warning; see the frames log.")
        if kind == "Begin":
            check_begin(event)
            begun.set()
        elif kind == "Termination":
            terminated.set()
            return
        turns.accept(event)
    raise TranscriptionError("AssemblyAI disconnected without a Termination message.")


async def send_audio(ws, audio, begun):
    await asyncio.wait_for(begun.wait(), timeout=20)
    loop = asyncio.get_running_loop()
    start = loop.time()
    samples_sent = 0
    # Each 100 ms packet contains only WAV data, never the RIFF header.
    while chunk := audio.readframes(1600):
        # A short last packet could violate the API's minimum chunk duration.
        chunk = chunk.ljust(3200, b"\x00")
        # Wait until this packet's audio duration has elapsed, avoiding bursts.
        samples_sent += len(chunk) // 2
        await asyncio.sleep(max(0, start + samples_sent / 16000 - loop.time()))
        await ws.send(chunk)
    # Silence closes speech at EOF, including speech with no trailing pause.
    for _ in range(10):
        samples_sent += 1600
        await asyncio.sleep(max(0, start + samples_sent / 16000 - loop.time()))
        await ws.send(b"\x00" * 3200)
    await ws.send('{"type":"ForceEndpoint"}')
    await ws.send('{"type":"Terminate"}')


async def stream(audio, audit, key):
    uri = "wss://streaming.assemblyai.com/v3/ws?" + urlencode(parameters())
    turns = Turns()
    begun, terminated = asyncio.Event(), asyncio.Event()
    async with connect(
        uri, additional_headers={"Authorization": key, "AssemblyAI-Version": "2025-05-12"},
        open_timeout=20, close_timeout=5, max_size=8 * 1024 * 1024,
    ) as ws:
        reader = asyncio.create_task(receive(ws, audit, turns, begun, terminated))
        writer = asyncio.create_task(send_audio(ws, audio, begun))
        try:
            done, _ = await asyncio.wait([reader, writer], return_when=asyncio.FIRST_COMPLETED)
            if reader in done:
                await reader  # Propagate errors immediately, cancelling audio transmission.
                if not writer.done():
                    raise TranscriptionError("AssemblyAI terminated before all audio was sent.")
            await writer
            await asyncio.wait_for(reader, timeout=30)
            if not terminated.is_set():
                raise TranscriptionError("No Termination acknowledgment received.")
        finally:
            for task in (reader, writer):
                task.cancel()
            await asyncio.gather(reader, writer, return_exceptions=True)
    return turns.result()


def write_result(path, result):
    # Only publish a complete transcript after successful termination.
    fd, temp = tempfile.mkstemp(prefix=".transcript-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(result, output, ensure_ascii=False)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temp)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
    if not key:
        raise TranscriptionError("ASSEMBLYAI_API_KEY is required.")
    audit_path = Path(str(args.output) + ".frames.jsonl")
    if args.audio.resolve() in (args.output.resolve(), audit_path.resolve()):
        raise TranscriptionError("Input audio and output paths must be different.")
    with wave.open(str(args.audio), "rb") as audio:
        if (audio.getframerate(), audio.getnchannels(), audio.getsampwidth(), audio.getcomptype()) != (16000, 1, 2, "NONE"):
            raise TranscriptionError("Audio must be an uncompressed 16 kHz mono 16-bit PCM WAV.")
        if audio.getnframes() == 0:
            raise TranscriptionError("Audio is empty.")
        # Restrict both new and existing audit files to the current user.
        fd = os.open(audit_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as audit:
            os.fchmod(audit.fileno(), 0o600)
            result = asyncio.run(stream(audio, audit, key))
    write_result(args.output, result)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Transcription interrupted.", file=sys.stderr)
        sys.exit(130)
    except TranscriptionError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        # Never print raw provider messages or credentials to the terminal.
        print(f"Transcription failed ({type(exc).__name__}); check input, network, and frames log.", file=sys.stderr)
        sys.exit(1)
