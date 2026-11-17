#!/usr/bin/env python3
"""Paced PCM streaming with provider-side redaction and a lossless message log."""

import argparse
import asyncio
import contextlib
import json
import os
from pathlib import Path
import sys
import tempfile
import wave
from urllib.parse import urlencode

from websockets.asyncio.client import connect


# Keep clinical content (drug, medical_condition, medical_process) readable.
PII_POLICIES = [
    "person_name", "phone_number", "us_social_security_number", "email_address",
    "location", "date_of_birth", "date", "date_interval", "person_age", "healthcare_number",
    "account_number", "banking_information", "credit_card_number",
    "credit_card_expiration", "credit_card_cvv", "drivers_license",
    "passport_number", "password", "username", "ip_address", "url", "vehicle_id",
    "number_sequence",
]


class StreamFailure(Exception):
    """A failure whose message is safe to display without transcript contents."""


class Turns:
    def __init__(self):
        self.final = {}
        self.pending = set()

    def receive(self, event):
        kind = event.get("type")
        if kind == "Turn":
            order = event["turn_order"]
            if event.get("end_of_turn"):
                old = self.final.get(order)
                # An unformatted retransmission must not undo formatted text.
                if not old or not old.get("turn_is_formatted") or event.get("turn_is_formatted"):
                    self.final[order] = event
                self.pending.discard(order)
            elif order not in self.final and event.get("transcript", "").strip():
                self.pending.add(order)
        elif kind == "SpeakerRevision":
            for revision in event.get("revisions", []):
                original = self.final.get(revision["turn_order"])
                if original is not None:
                    original = dict(original)
                    original["speaker_label"] = revision.get("speaker_label")
                    # Speaker revisions change attribution, not text/formatting.
                    revised_words = revision.get("words", [])
                    if len(revised_words) == len(original.get("words", [])):
                        original["words"] = [
                            dict(word, speaker=new.get("speaker"))
                            for word, new in zip(original["words"], revised_words)
                        ]
                    self.final[revision["turn_order"]] = original

    def result(self):
        if self.pending:
            raise StreamFailure("Session ended with unfinished transcript turns.")
        result = []
        for order in sorted(self.final):
            turn = self.final[order]
            text = turn.get("transcript", "").strip()
            if not text:
                continue
            words = turn.get("words", [])
            labels = {w.get("speaker") for w in words if w.get("speaker") is not None}
            if len(labels) > 1:
                # A server turn may span a speaker change. Preserve its word order.
                groups = []
                for word in words:
                    label = word.get("speaker") or turn.get("speaker_label") or "unknown"
                    if not groups or groups[-1]["speaker"] != str(label):
                        groups.append({"speaker": str(label), "text": ""})
                    groups[-1]["text"] += (" " if groups[-1]["text"] else "") + word["text"]
                result.extend(groups)
            else:
                label = turn.get("speaker_label")
                if label is None:
                    label = next(iter(labels), "unknown")
                result.append({"speaker": str(label), "text": text})
        return {"turns": result}


async def send_audio(ws, audio):
    # WAV headers are never sent. 100 ms PCM chunks meet the 50–1000 ms limit.
    frames_per_chunk = 1600
    loop = asyncio.get_running_loop()
    deadline = loop.time()
    while True:
        chunk = audio.readframes(frames_per_chunk)
        if not chunk:
            break
        chunk = chunk.ljust(frames_per_chunk * 2, b"\0")
        deadline += 0.1
        await asyncio.sleep(max(0, deadline - loop.time()))
        await ws.send(chunk)
        # Avoid catch-up bursts if a write was delayed by network backpressure.
        deadline = max(deadline, loop.time())
    # Give endpointing/formatting time to settle, including speech at EOF.
    for _ in range(10):
        deadline += 0.1
        await asyncio.sleep(max(0, deadline - loop.time()))
        await ws.send(b"\0" * 3200)
        deadline = max(deadline, loop.time())
    await ws.send('{"type":"ForceEndpoint"}')
    await asyncio.sleep(1)
    await ws.send('{"type":"Terminate"}')


async def receive(ws, audit, turns, began):
    terminated = False
    async for raw in ws:
        # Write the original payload, without rebuilding it from parsed events.
        payload = raw.encode("utf-8") if isinstance(raw, str) else raw
        audit.write(payload + b"\n")
        audit.flush()
        event = json.loads(payload)
        if not isinstance(event, dict):
            raise StreamFailure("AssemblyAI returned a non-object message.")
        kind = event.get("type")
        if kind == "Begin":
            config = event.get("configuration", {})
            if (config.get("redact_pii") is not True
                    or config.get("speaker_labels") is not True
                    or config.get("domain") != "medical-v1"):
                raise StreamFailure("AssemblyAI did not confirm the required features.")
            began.set()
        elif kind == "Error" or event.get("error"):
            raise StreamFailure("AssemblyAI reported an error; see the frame log.")
        elif kind == "Warning":
            # Never proceed if the provider warns that an option was ignored.
            raise StreamFailure("AssemblyAI reported a warning; see the frame log.")
        elif kind == "Termination":
            terminated = True
        turns.receive(event)
        if terminated:
            return
    raise StreamFailure("AssemblyAI closed the connection without Termination.")


async def transcribe(audio, audit, key):
    params = {
        "sample_rate": 16000,
        "encoding": "pcm_s16le",
        "speech_model": "universal-3-6-pro",
        "domain": "medical-v1",
        "mode": "max_accuracy",
        "speaker_labels": "true",
        "redact_pii": "true",
        "redact_pii_policies": json.dumps(PII_POLICIES),
        "redact_pii_sub": "entity_name",
        "include_partial_turns": "false",
        "format_turns": "true",
        "customer_support_audio_capture": "false",
    }
    url = "wss://streaming.assemblyai.com/v3/ws?" + urlencode(params)
    turns = Turns()
    async with connect(
        url, additional_headers={"Authorization": key, "AssemblyAI-Version": "2025-05-12"},
        open_timeout=20, close_timeout=5, max_size=8 * 1024 * 1024,
    ) as ws:
        began = asyncio.Event()
        reader = asyncio.create_task(receive(ws, audit, turns, began))
        begin_wait = asyncio.create_task(began.wait())
        sender = None
        try:
            done, _ = await asyncio.wait(
                [reader, begin_wait], timeout=20, return_when=asyncio.FIRST_COMPLETED
            )
            if reader in done:
                await reader
                raise StreamFailure("Session terminated before audio was sent.")
            if not began.is_set():
                raise StreamFailure("Timed out waiting for AssemblyAI Begin.")
            sender = asyncio.create_task(send_audio(ws, audio))
            done, _ = await asyncio.wait([reader, sender], return_when=asyncio.FIRST_COMPLETED)
            if reader in done:
                await reader
                if not sender.done():
                    raise StreamFailure("Session terminated before all audio was sent.")
            await sender
            await asyncio.wait_for(reader, timeout=30)
        finally:
            for task in (reader, begin_wait, sender):
                if task is not None:
                    task.cancel()
            await asyncio.gather(*(t for t in (reader, begin_wait, sender) if t is not None),
                                 return_exceptions=True)
    return turns.result()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
    if not key:
        raise StreamFailure("ASSEMBLYAI_API_KEY is required.")
    audit_path = Path(str(args.output) + ".frames.jsonl")
    if args.audio.resolve() in (args.output.resolve(), audit_path.resolve()):
        raise StreamFailure("Input and output paths must be different.")
    # Refuse to overwrite existing records, including a stale successful output.
    if args.output.exists() or audit_path.exists():
        raise StreamFailure("Output or frame log already exists; choose a new output path.")
    with wave.open(str(args.audio), "rb") as audio:
        if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate(), audio.getcomptype()) != (1, 2, 16000, "NONE"):
            raise StreamFailure("Audio must be uncompressed 16 kHz mono 16-bit PCM WAV.")
        if audio.getnframes() == 0:
            raise StreamFailure("Audio file is empty.")
        with audit_path.open("xb") as audit:
            result = asyncio.run(transcribe(audio, audit, key))
    # Only publish a complete result after a successful server termination.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=args.output.parent,
                                         prefix=".transcript-", delete=False) as out:
            temporary = out.name
            json.dump(result, out, ensure_ascii=False)
            out.write("\n")
        os.replace(temporary, args.output)
    finally:
        if temporary:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(temporary)
    print(f"Transcription complete: {len(result['turns'])} turns.", file=sys.stderr)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Transcription interrupted.", file=sys.stderr)
        sys.exit(130)
    except StreamFailure as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        # Provider exceptions can contain transcript content or request headers.
        print(f"Transcription failed ({type(exc).__name__}); no final output was written.", file=sys.stderr)
        sys.exit(1)
