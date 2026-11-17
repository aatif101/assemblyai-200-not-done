#!/usr/bin/env python3
"""Stream PCM WAV audio to AssemblyAI; persist only server-redacted responses."""

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
from urllib.parse import urlencode
import wave

from websockets.asyncio.client import connect


# Preserve clinical vocabulary; redact identifiers rather than drugs/conditions.
PII_POLICIES = [
    "person_name", "phone_number", "us_social_security_number", "date_of_birth",
    "email_address", "location", "healthcare_number", "account_number",
    "banking_information", "credit_card_number", "credit_card_expiration",
    "credit_card_cvv", "drivers_license", "passport_number", "password",
    "ip_address", "url", "username", "vehicle_id", "number_sequence",
]


def parameters():
    return {
        "sample_rate": "16000",
        "encoding": "pcm_s16le",
        "speech_model": "universal-streaming-english",
        "domain": "medical-v1",
        "speaker_labels": "true",
        "redact_pii": "true",
        "redact_pii_policies": json.dumps(PII_POLICIES),
        "redact_pii_sub": "entity_name",
        "format_turns": "true",
        "include_partial_turns": "false",
        "continuous_partials": "false",
        "customer_support_audio_capture": "false",
    }


class Transcript:
    def __init__(self):
        self.turns = {}
        self.revisions = {}

    def accept(self, event):
        if event.get("type") == "Turn" and event.get("end_of_turn"):
            order = event["turn_order"]
            previous = self.turns.get(order)
            # A formatted turn supersedes its unformatted version, even if an
            # older unformatted update happens to arrive later.
            if not previous or event.get("turn_is_formatted") or not previous.get("turn_is_formatted"):
                self.turns[order] = event
        elif event.get("type") == "SpeakerRevision":
            for revision in event.get("revisions", []):
                self.revisions[revision["turn_order"]] = revision

    def result(self):
        result = []
        for order, turn in sorted(self.turns.items()):
            text = turn.get("transcript", "")
            if not text.strip():
                continue
            revision = self.revisions.get(order, {})
            speaker = revision.get("speaker_label") or turn.get("speaker_label")
            words = revision.get("words") or turn.get("words", [])
            if not speaker:
                labels = {w.get("speaker") for w in words if w.get("speaker")}
                if len(labels) == 1:
                    speaker = labels.pop()
            result.append({"speaker": speaker or "Unknown", "text": text})
        return {"turns": result}


async def send_audio(ws, audio):
    loop = asyncio.get_running_loop()
    start = loop.time()
    sent_samples = 0
    while chunk := audio.readframes(1600):
        # WAV headers are never sent. Pad a short last chunk to the minimum
        # accepted 50 ms without discarding any audio.
        if len(chunk) < 1600:
            chunk += bytes(1600 - len(chunk))
        sent_samples += len(chunk) // 2
        await asyncio.sleep(max(0, start + sent_samples / 16000 - loop.time()))
        await ws.send(chunk)
    await ws.send(json.dumps({"type": "Terminate"}))


async def receive(ws, audit, transcript, begun, terminated):
    async for raw in ws:
        # Save the original payload, without SDK parsing/reserialization,
        # before interpreting it. No transcript text is printed to the console.
        wire = raw.encode("utf-8") if isinstance(raw, str) else raw
        audit.write(wire + b"\n")
        audit.flush()
        event = json.loads(wire)
        if not isinstance(event, dict):
            raise RuntimeError("AssemblyAI sent a non-object message")
        kind = event.get("type")
        if kind == "Begin":
            config = event.get("configuration", {})
            if (config.get("redact_pii") is not True
                    or config.get("speaker_labels") is not True
                    or config.get("domain") != "medical-v1"):
                raise RuntimeError("AssemblyAI did not confirm required privacy/medical/speaker settings")
            begun.set()
        elif kind == "Error" or event.get("error"):
            # Server error text may contain personal information; it belongs
            # only in the required audit log.
            raise RuntimeError("AssemblyAI returned an error; see the audit log")
        elif kind == "Warning":
            raise RuntimeError("AssemblyAI returned a warning; see the audit log")
        transcript.accept(event)
        if kind == "Termination":
            terminated.set()
            return
    raise RuntimeError("AssemblyAI closed before acknowledging termination")


async def stream(audio, audit, key):
    transcript = Transcript()
    begun, terminated = asyncio.Event(), asyncio.Event()
    uri = "wss://streaming.assemblyai.com/v3/ws?" + urlencode(parameters())
    async with connect(
        uri, additional_headers={"Authorization": key, "AssemblyAI-Version": "2025-05-12"},
        open_timeout=20, close_timeout=5, max_size=4 * 1024 * 1024,
    ) as ws:
        reader = asyncio.create_task(receive(ws, audit, transcript, begun, terminated))

        async def produce():
            await asyncio.wait_for(begun.wait(), 20)
            await send_audio(ws, audio)
            await asyncio.wait_for(terminated.wait(), 30)

        writer = asyncio.create_task(produce())
        try:
            # Reader failures interrupt sending immediately; writer failures
            # interrupt receiving. Success requires both tasks to finish.
            await asyncio.gather(reader, writer)
        finally:
            for task in (reader, writer):
                if not task.done():
                    task.cancel()
            await asyncio.gather(reader, writer, return_exceptions=True)
    return transcript.result()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("ASSEMBLYAI_API_KEY is required")
    audit_path = Path(str(args.output) + ".frames.jsonl")
    if args.audio.resolve() in (args.output.resolve(), audit_path.resolve()):
        raise RuntimeError("Input audio and output paths must be different")
    # Refuse overwriting earlier audit records or transcripts.
    if args.output.exists() or audit_path.exists():
        raise RuntimeError("Output or audit log already exists; choose a new output path")
    with wave.open(str(args.audio), "rb") as audio:
        if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate(), audio.getcomptype()) != (1, 2, 16000, "NONE"):
            raise RuntimeError("Audio must be uncompressed 16 kHz mono 16-bit PCM WAV")
        if audio.getnframes() == 0:
            raise RuntimeError("Audio file is empty")
        fd = os.open(audit_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as audit:
            result = asyncio.run(stream(audio, audit, key))
    # Publish a complete result atomically; failures leave the audit for diagnosis.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=args.output.parent, delete=False) as out:
            temporary = out.name
            json.dump(result, out, ensure_ascii=False)
            out.write("\n")
        os.replace(temporary, args.output)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Transcription interrupted", file=sys.stderr)
        sys.exit(130)
    except Exception as exc:
        # Avoid echoing API payloads or credentials from library exceptions.
        message = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
        print(f"Transcription failed: {message}", file=sys.stderr)
        sys.exit(1)
