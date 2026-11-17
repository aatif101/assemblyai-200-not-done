#!/usr/bin/env python3
"""Paced WAV -> AssemblyAI v3, with a lossless received-message audit log."""
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

MODEL = "universal-3-6-pro"
ENDPOINT = "wss://streaming.assemblyai.com/v3/ws"
PARAMS = {
    "speech_model": MODEL,
    "sample_rate": 16000,
    "encoding": "pcm_s16le",
    "domain": "medical-v1",
    "speaker_labels": "true",
    "speaker_labels_revision_interval_ms": 300000,
    "redact_pii": "true",
    # Omit policies to redact ALL supported entity types, including new ones.
    "redact_pii_sub": "entity_name",
    "include_partial_turns": "false",
    # Override the diarization profile's short silence defaults so pauses within
    # phone numbers and other entities are less likely to split them into turns.
    "min_turn_silence": 640,
    "max_turn_silence": 2560,
}


class TranscriptionError(Exception):
    pass


class Turns:
    def __init__(self):
        self.turns = {}
        self.labels = {}

    def accept(self, message):
        kind = message.get("type")
        if kind == "Turn":
            if message.get("end_of_turn") is not True:
                raise TranscriptionError("Server sent a partial despite partials being disabled.")
            order = message["turn_order"]
            self.turns[order] = {
                "speaker": self.labels.get(order, message.get("speaker_label")) or "UNKNOWN",
                "text": message["transcript"],
            }
        elif kind == "SpeakerRevision":
            for revision in message["revisions"]:
                order = revision["turn_order"]
                label = revision.get("speaker_label") or "UNKNOWN"
                self.labels[order] = label
                if order in self.turns:
                    self.turns[order]["speaker"] = label

    def result(self):
        return {"turns": [self.turns[key] for key in sorted(self.turns)]}


def check_begin(message):
    if message.get("type") != "Begin":
        raise TranscriptionError("Expected AssemblyAI Begin confirmation.")
    config = message.get("configuration", {})
    expected = {"model": MODEL, "domain": "medical-v1", "speaker_labels": True, "redact_pii": True}
    if any(config.get(key) != value for key, value in expected.items()):
        raise TranscriptionError("AssemblyAI did not confirm the requested model, Medical Mode, speaker labels, and PII masking; no audio sent.")


async def receive(ws, audit):
    raw = await asyncio.wait_for(ws.recv(), timeout=60)
    # Write the original payload, never a parsed/reserialized version. No SDK
    # event conversion can discard fields from the audit trail.
    payload = raw.encode("utf-8") if isinstance(raw, str) else raw
    audit.write(payload + b"\n")
    audit.flush()
    message = json.loads(payload)
    if not isinstance(message, dict):
        raise TranscriptionError("AssemblyAI sent a non-object message.")
    if message.get("type") == "Error" or message.get("error"):
        # Do not copy arbitrary provider text (potentially sensitive) to stderr.
        raise TranscriptionError("AssemblyAI reported an error; see the received-message audit log.")
    return message


async def send_audio(ws, audio):
    loop = asyncio.get_running_loop()
    deadline = loop.time()
    while True:
        chunk = audio.readframes(800)  # 50ms, raw PCM; never send the WAV header.
        if not chunk:
            break
        # Pad the final chunk to the API's minimum 50ms size.
        chunk = chunk.ljust(1600, b"\0")
        # Wait for the chunk's duration before sending, just as a capture device
        # would. Reset after stalls to avoid catch-up bursts above real time.
        deadline = max(deadline, loop.time()) + 0.05
        await asyncio.sleep(max(0, deadline - loop.time()))
        await ws.send(chunk)
    # A paced silence tail lets the last spoken turn finish naturally.
    for _ in range(60):
        await asyncio.sleep(0.05)
        await ws.send(b"\0" * 1600)
    await ws.send('{"type":"Terminate"}')


async def transcribe(audio, audit, api_key):
    turns = Turns()
    async with connect(
        ENDPOINT + "?" + urlencode(PARAMS),
        additional_headers={"Authorization": api_key},
        open_timeout=30, close_timeout=5, max_size=None,
    ) as ws:
        check_begin(await receive(ws, audit))
        sender = asyncio.create_task(send_audio(ws, audio))

        async def collect():
            while True:
                message = await receive(ws, audit)
                turns.accept(message)
                if message.get("type") == "Termination":
                    if not sender.done():
                        raise TranscriptionError("AssemblyAI terminated before all audio was sent.")
                    return

        receiver = asyncio.create_task(collect())
        try:
            # Propagate either task's failures promptly; receive concurrently
            # while sending and continue through final speaker revisions.
            await asyncio.gather(sender, receiver)
        finally:
            for task in (sender, receiver):
                task.cancel()
            await asyncio.gather(sender, receiver, return_exceptions=True)
    return turns.result()


def write_result(path, result):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as output:
            temporary = output.name
            json.dump(result, output, ensure_ascii=False)
            output.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    try:
        api_key = os.environ.get("ASSEMBLYAI_API_KEY")
        if not api_key:
            raise TranscriptionError("ASSEMBLYAI_API_KEY is required.")
        audit_path = Path(str(args.output) + ".frames.jsonl")
        if args.audio.resolve() in (args.output.resolve(), audit_path.resolve()):
            raise TranscriptionError("Output paths must differ from the input recording.")
        with wave.open(str(args.audio), "rb") as audio:
            if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate(), audio.getcomptype()) != (1, 2, 16000, "NONE"):
                raise TranscriptionError("Input must be a 16 kHz mono 16-bit PCM WAV.")
            if not audio.getnframes():
                raise TranscriptionError("Input WAV is empty.")
            # Restrict permissions for both the audit and final transcript.
            fd = os.open(audit_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "wb") as audit:
                result = asyncio.run(transcribe(audio, audit, api_key))
        write_result(args.output, result)
    except KeyboardInterrupt:
        print("Transcription interrupted.", file=sys.stderr)
        return 130
    except Exception as exc:
        detail = str(exc) if isinstance(exc, TranscriptionError) else type(exc).__name__
        print(f"Transcription failed: {detail}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
