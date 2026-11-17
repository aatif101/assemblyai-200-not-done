#!/usr/bin/env python3
"""Paced PCM streaming with server-side medical recognition, diarization and PII masking."""
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

# Keep clinical vocabulary while masking identifying personal information.
PII_POLICIES = [
    "person_name", "phone_number", "us_social_security_number", "email_address",
    "date_of_birth", "person_age", "location", "account_number",
    "banking_information", "credit_card_number", "credit_card_expiration",
    "credit_card_cvv", "drivers_license", "healthcare_number", "passport_number",
    "ip_address", "password", "username", "url", "vehicle_id",
    # Catch identifiers split across turns and address/unit number fragments.
    # This deliberately also masks numeric clinical values.
    "number_sequence", "date",
]
PARAMETERS = {
    "sample_rate": 16000,
    "encoding": "pcm_s16le",
    "speech_model": "universal-streaming-english",
    "domain": "medical-v1",
    "speaker_labels": "true",
    "redact_pii": "true",
    "redact_pii_policies": json.dumps(PII_POLICIES),
    "redact_pii_sub": "entity_name",
    # Suppress partials as an additional precaution: persist only server-masked text.
    "include_partial_turns": "false",
    "format_turns": "true",
    "customer_support_audio_capture": "false",
}


class StreamFailure(Exception):
    pass


class Turns:
    def __init__(self):
        self.by_order = {}
        self.revisions = {}

    def accept(self, event):
        if event.get("type") == "Turn" and event.get("end_of_turn"):
            order = event["turn_order"]
            old = self.by_order.get(order)
            # An unformatted retransmission must not overwrite formatted text.
            if not old or event.get("turn_is_formatted") or not old.get("turn_is_formatted"):
                self.by_order[order] = event
        elif event.get("type") == "SpeakerRevision":
            for revision in event.get("revisions", []):
                self.revisions[revision["turn_order"]] = revision

    def result(self):
        result = []
        for order, turn in sorted(self.by_order.items()):
            text = turn.get("transcript", "")
            if not text.strip():
                continue
            speaker_info = self.revisions.get(order, turn)
            speaker = speaker_info.get("speaker_label")
            if speaker is None:
                labels = {w.get("speaker") for w in speaker_info.get("words", [])} - {None}
                speaker = next(iter(labels)) if len(labels) == 1 else "Unknown"
            result.append({"speaker": str(speaker), "text": text})
        return {"turns": result}


def open_private(path):
    # Exclusive creation avoids truncating an existing audit log or following symlinks.
    return os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8", newline="\n")


async def receive(ws, audit, turns, begun, terminated):
    async for raw in ws:
        if not isinstance(raw, str):
            raise StreamFailure("Unexpected binary server message")
        # Write the original wire message, including unknown fields and event types.
        audit.write(raw + "\n")
        audit.flush()
        event = json.loads(raw)
        kind = event.get("type")
        if kind == "Begin":
            config = event.get("configuration", {})
            for key, wanted in (("speaker_labels", True), ("redact_pii", True), ("domain", "medical-v1")):
                if key in config and config[key] != wanted:
                    raise StreamFailure("Server did not enable required setting: " + key)
            begun.set()
        elif kind in ("Error", "Warning") or event.get("error"):
            # Never echo a server payload: it may contain personal data.
            raise StreamFailure("AssemblyAI returned an error or configuration warning; see audit log")
        turns.accept(event)
        if kind == "Termination":
            terminated.set()
            return
    raise StreamFailure("Connection closed without a Termination message")


async def send_audio(ws, audio):
    # 100 ms binary chunks; never send the WAV header. Pad a short final chunk
    # so every packet satisfies AssemblyAI's minimum chunk-duration requirement.
    chunk_frames = 1600
    loop = asyncio.get_running_loop()
    deadline = loop.time()
    while data := audio.readframes(chunk_frames):
        if len(data) < chunk_frames * 2:
            data += b"\0" * (chunk_frames * 2 - len(data))
        # A slow connection must not cause a later burst of catch-up packets.
        deadline = max(deadline, loop.time()) + 0.1
        await ws.send(data)
        await asyncio.sleep(max(0, deadline - loop.time()))
    await ws.send(json.dumps({"type": "Terminate"}))


async def stream(audio, audit, key):
    turns = Turns()
    begun, terminated = asyncio.Event(), asyncio.Event()
    uri = "wss://streaming.assemblyai.com/v3/ws?" + urlencode(PARAMETERS)
    async with connect(uri, additional_headers={"Authorization": key}, open_timeout=20,
                       close_timeout=5, max_size=8 * 1024 * 1024) as ws:
        receiver = asyncio.create_task(receive(ws, audit, turns, begun, terminated))
        sender = None
        begin_wait = asyncio.create_task(begun.wait())
        try:
            done, _ = await asyncio.wait([receiver, begin_wait], timeout=20,
                                         return_when=asyncio.FIRST_COMPLETED)
            if receiver in done:
                await receiver
                raise StreamFailure("Session terminated before audio was sent")
            if not begun.is_set():
                raise StreamFailure("Timed out waiting for Begin")
            sender = asyncio.create_task(send_audio(ws, audio))
            done, _ = await asyncio.wait([receiver, sender], return_when=asyncio.FIRST_COMPLETED)
            if receiver in done:
                await receiver
                if not sender.done():
                    raise StreamFailure("Session terminated before all audio was sent")
            await sender
            # Termination flushes the last turn. Do not close as soon as EOF is sent.
            await asyncio.wait_for(asyncio.shield(receiver), timeout=60)
            if not terminated.is_set():
                raise StreamFailure("Missing Termination")
        finally:
            tasks = [t for t in (receiver, sender, begin_wait) if t is not None]
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    return turns.result()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    key = os.environ.get("ASSEMBLYAI_API_KEY")
    if not key:
        parser.error("ASSEMBLYAI_API_KEY is required")
    audit_path = Path(str(args.output) + ".frames.jsonl")
    if args.output.exists() or audit_path.exists():
        parser.error("Output or audit path already exists; choose a new output path")
    temporary = None
    try:
        with wave.open(str(args.audio), "rb") as audio:
            if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate(), audio.getcomptype()) != (1, 2, 16000, "NONE"):
                raise StreamFailure("Input must be an uncompressed 16 kHz mono 16-bit PCM WAV")
            if audio.getnframes() == 0:
                raise StreamFailure("Input contains no audio")
            with open_private(audit_path) as audit:
                result = asyncio.run(stream(audio, audit, key))
        # Publish final JSON only after a clean, fully drained session.
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=args.output.parent,
                                         prefix=".transcript-", delete=False) as out:
            temporary = out.name
            json.dump(result, out, ensure_ascii=False)
            out.write("\n")
        os.replace(temporary, args.output)
        temporary = None
        return 0
    except KeyboardInterrupt:
        print("Transcription interrupted; final output was not written", file=sys.stderr)
        return 130
    except Exception as exc:
        # Exception strings from transports can include URLs or credentials.
        if isinstance(exc, StreamFailure):
            print(str(exc), file=sys.stderr)
        else:
            print(f"Transcription failed ({type(exc).__name__}); final output was not written", file=sys.stderr)
        return 1
    finally:
        if temporary:
            with contextlib.suppress(OSError):
                os.unlink(temporary)


if __name__ == "__main__":
    sys.exit(main())
