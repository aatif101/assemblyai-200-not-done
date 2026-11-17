#!/usr/bin/env python3
"""Pace PCM audio into AssemblyAI v3; persist only server-redacted messages."""

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


# Keep clinical vocabulary, but redact identifiers and other personal details.
PII_POLICIES = [
    "person_name", "phone_number", "us_social_security_number", "email_address",
    "location", "date", "date_of_birth", "person_age", "healthcare_number",
    "account_number", "banking_information", "credit_card_number",
    "credit_card_expiration", "credit_card_cvv", "drivers_license",
    "passport_number", "password", "ip_address", "url", "username", "vehicle_id",
    "number_sequence",
]


class TranscriptionError(Exception):
    pass


def session_url():
    parameters = {
        "sample_rate": 16000,
        "encoding": "pcm_s16le",
        "speech_model": "universal-3-6-pro",
        "domain": "medical-v1",
        "mode": "max_accuracy",
        "speaker_labels": "true",
        "format_turns": "true",
        "redact_pii": "true",
        "redact_pii_policies": json.dumps(PII_POLICIES),
        "redact_pii_sub": "entity_name",
        # Provisional text must never reach the unmodified audit log.
        "include_partial_turns": "false",
        "continuous_partials": "false",
        "customer_support_audio_capture": "false",
    }
    return "wss://streaming.assemblyai.com/v3/ws?" + urlencode(parameters)


class Turns:
    def __init__(self):
        self.turns = {}

    def accept(self, message):
        if message.get("type") == "Turn":
            if not message.get("end_of_turn"):
                return
            order = message["turn_order"]
            previous = self.turns.get(order)
            # A late unformatted duplicate must not undo a formatted final.
            if previous and previous.get("turn_is_formatted") and not message.get("turn_is_formatted"):
                return
            self.turns[order] = message
        elif message.get("type") == "SpeakerRevision":
            for revision in message.get("revisions", []):
                previous = self.turns.get(revision["turn_order"])
                if previous is not None:
                    previous["speaker_label"] = revision.get("speaker_label")
                    if revision.get("words"):
                        # Revisions change attribution, never the redacted text.
                        by_time = {(w["start"], w["end"]): w.get("speaker")
                                   for w in revision["words"]}
                        for word in previous.get("words", []):
                            key = (word["start"], word["end"])
                            if key in by_time:
                                word["speaker"] = by_time[key]

    def result(self):
        result = []
        for _, turn in sorted(self.turns.items()):
            text = turn.get("transcript", "").strip()
            if not text:
                continue
            words = turn.get("words", [])
            labels = {w.get("speaker") for w in words if w.get("speaker")}
            if len(labels) <= 1:
                speaker = turn.get("speaker_label") or next(iter(labels), "UNKNOWN")
                result.append({"speaker": speaker, "text": text})
                continue
            # A server turn can contain multiple voices. Split using offsets into
            # the redacted transcript, never by rebuilding from raw word text.
            segments = []
            cursor = 0
            speaker = turn.get("speaker_label") or "UNKNOWN"
            for word in words:
                token = word.get("text", "")
                start = text.find(token, cursor) if token else -1
                if start < 0:
                    raise TranscriptionError("Cannot align speaker labels with redacted transcript")
                label = word.get("speaker") or speaker
                if not segments or label != speaker:
                    segments.append([label, cursor])
                speaker = label
                cursor = start + len(token)
            for index, (label, start) in enumerate(segments):
                end = segments[index + 1][1] if index + 1 < len(segments) else len(text)
                piece = text[start:end].strip()
                if piece:
                    result.append({"speaker": label, "text": piece})
        return {"turns": result}


async def send_audio(ws, audio):
    loop = asyncio.get_running_loop()
    origin = loop.time()
    sent_samples = 0
    # 100 ms messages; consume the WAV header locally, send only signed PCM.
    while chunk := audio.readframes(1600):
        sent_samples += len(chunk) // 2
        await asyncio.sleep(max(0, origin + sent_samples / 16000 - loop.time()))
        await ws.send(chunk)
    if sent_samples != audio.getnframes():
        raise TranscriptionError("WAV file is truncated")
    # Terminate flushes the final turn. Continue receiving through Termination.
    await ws.send('{"type":"Terminate"}')


async def receive(ws, audit, turns, begun):
    while True:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=60)
        except TimeoutError:
            raise TranscriptionError("Timed out waiting for AssemblyAI") from None
        # Write the original payload, preserving all fields and their order.
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        audit.write(raw + "\n")
        audit.flush()
        message = json.loads(raw)
        kind = message.get("type")
        if kind == "Begin":
            config = message.get("configuration", {})
            if config.get("redact_pii") is False or config.get("speaker_labels") is False:
                raise TranscriptionError("AssemblyAI did not enable the required session features")
            begun.set()
        elif kind == "Error" or message.get("error"):
            # Error text may echo input; retain it only in the required audit.
            raise TranscriptionError("AssemblyAI reported an error; see the audit log")
        elif kind == "Warning":
            raise TranscriptionError("AssemblyAI reported a configuration warning; see the audit log")
        elif kind == "Termination":
            return
        turns.accept(message)


async def stream(audio, audit, key):
    turns = Turns()
    async with connect(
        session_url(), additional_headers={"Authorization": key,
                                          "AssemblyAI-Version": "2025-05-12"},
        open_timeout=20, close_timeout=5, max_size=8 * 1024 * 1024,
    ) as ws:
        begun = asyncio.Event()
        receiver = asyncio.create_task(receive(ws, audit, turns, begun))
        ready = asyncio.create_task(begun.wait())
        sender = None
        try:
            done, _ = await asyncio.wait([receiver, ready], timeout=20,
                                         return_when=asyncio.FIRST_COMPLETED)
            if receiver in done:
                await receiver
                raise TranscriptionError("Session ended before audio could be sent")
            if ready not in done:
                raise TranscriptionError("Timed out waiting for session to begin")
            sender = asyncio.create_task(send_audio(ws, audio))
            done, _ = await asyncio.wait([sender, receiver], return_when=asyncio.FIRST_COMPLETED)
            if receiver in done and not sender.done():
                await receiver
                raise TranscriptionError("Session ended before all audio was sent")
            await sender
            await asyncio.wait_for(receiver, timeout=60)
        finally:
            for task in (ready, sender, receiver):
                if task is not None:
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await task
    return turns.result()


def write_result(output, result):
    # Publish only a completely drained, successful session.
    fd, temporary = tempfile.mkstemp(prefix=".transcript-", dir=output.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(result, handle, ensure_ascii=False)
            handle.write("\n")
        os.replace(temporary, output)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
    if not key:
        raise TranscriptionError("Set ASSEMBLYAI_API_KEY in the environment")
    audit_path = Path(str(args.output) + ".frames.jsonl")
    paths = [p.resolve() for p in (args.audio, args.output, audit_path)]
    if len(set(paths)) != 3:
        raise TranscriptionError("Input, output, and audit paths must be different")
    with wave.open(str(args.audio), "rb") as audio:
        if (audio.getframerate(), audio.getnchannels(), audio.getsampwidth(), audio.getcomptype()) != (16000, 1, 2, "NONE"):
            raise TranscriptionError("Audio must be 16 kHz mono signed 16-bit PCM WAV")
        if not audio.getnframes():
            raise TranscriptionError("Audio is empty")
        with audit_path.open("w", encoding="utf-8", newline="\n") as audit:
            result = asyncio.run(stream(audio, audit, key))
    write_result(args.output, result)
    print(f"Saved {len(result['turns'])} turns and the AssemblyAI message audit.", file=sys.stderr)


if __name__ == "__main__":
    os.umask(0o077)
    try:
        main()
    except KeyboardInterrupt:
        print("Transcription interrupted", file=sys.stderr)
        sys.exit(130)
    except Exception as exc:
        # Never print provider payloads, call text, or authentication headers.
        detail = str(exc) if isinstance(exc, TranscriptionError) else type(exc).__name__
        print(f"Transcription failed: {detail}", file=sys.stderr)
        sys.exit(1)
