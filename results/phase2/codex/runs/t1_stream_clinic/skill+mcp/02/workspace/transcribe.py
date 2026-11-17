#!/usr/bin/env python3
"""Stream a PCM WAV to AssemblyAI; preserve received JSON and collect finals."""
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


class TranscriptionError(Exception):
    pass


def validate_begin(message):
    if message.get("type") != "Begin":
        raise TranscriptionError("Expected AssemblyAI Begin message; see frames audit log.")
    config = message.get("configuration", {})
    required = {"model": MODEL, "domain": "medical-v1", "speaker_labels": True,
                "redact_pii": True}
    if any(config.get(key) != value for key, value in required.items()):
        raise TranscriptionError("AssemblyAI did not confirm required model, medical mode, speaker labels, and PII redaction; no audio sent.")


class Turns:
    def __init__(self):
        self.turns = {}
        self.labels = {}

    def accept(self, message):
        kind = message.get("type")
        if kind == "Turn":
            if message.get("end_of_turn") is not True:
                raise TranscriptionError("Unexpected partial turn despite include_partial_turns=false.")
            order = message["turn_order"]
            # Never rebuild text from words: only use the server's redacted transcript.
            self.turns[order] = {"speaker": self.labels.get(order, message.get("speaker_label")) or "UNKNOWN",
                                 "text": message["transcript"]}
        elif kind == "SpeakerRevision":
            for revision in message["revisions"]:
                order = revision["turn_order"]
                label = revision.get("speaker_label") or "UNKNOWN"
                self.labels[order] = label
                if order in self.turns:
                    self.turns[order]["speaker"] = label
        elif kind == "Error" or message.get("error"):
            # Server errors remain in the audit log; don't echo arbitrary server text.
            raise TranscriptionError("AssemblyAI reported an error; see frames audit log.")

    def result(self):
        return {"turns": [self.turns[k] for k in sorted(self.turns)]}


async def send_audio(ws, audio):
    rate = audio.getframerate()
    chunk_frames = rate // 20  # 50ms, comfortably below the 128KB message limit.
    chunk_bytes = chunk_frames * 2
    deadline = asyncio.get_running_loop().time()
    while True:
        data = audio.readframes(chunk_frames)
        if not data:
            break
        # Pace by a monotonic clock; do not send the WAV container/header.
        await asyncio.sleep(max(0, deadline - asyncio.get_running_loop().time()))
        await ws.send(data.ljust(chunk_bytes, b"\0"))
        # Don't burst to catch up after a slow network send.
        deadline = max(deadline + .05, asyncio.get_running_loop().time())
    await asyncio.sleep(max(0, deadline - asyncio.get_running_loop().time()))
    await ws.send(json.dumps({"type": "Terminate"}))


async def transcribe(audio, api_key, audit):
    params = {"sample_rate": audio.getframerate(), "encoding": "pcm_s16le",
              "speech_model": MODEL, "domain": "medical-v1", "speaker_labels": "true",
              "speaker_labels_revision_interval_ms": 300000,
              "redact_pii": "true", "include_partial_turns": "false",
              "redact_pii_sub": "entity_name",
              # Omit policies to redact ALL supported PII categories.
              "min_turn_silence": 800, "max_turn_silence": 3600}
    turns = Turns()
    async with connect(ENDPOINT + "?" + urlencode(params),
                       additional_headers={"Authorization": api_key},
                       open_timeout=20, close_timeout=5, max_size=8 * 1024 * 1024) as ws:
        async def receive():
            raw = await asyncio.wait_for(ws.recv(), timeout=40)
            # Preserve the actual server payload, without SDK conversion or reserialization.
            payload = raw.encode("utf-8") if isinstance(raw, str) else raw
            audit.write(payload + b"\n")
            audit.flush()
            message = json.loads(payload)
            if not isinstance(message, dict):
                raise TranscriptionError("Expected a JSON object from AssemblyAI.")
            return message

        validate_begin(await receive())

        async def collect():
            while True:
                message = await receive()
                turns.accept(message)
                if message.get("type") == "Termination":
                    return

        sender = asyncio.create_task(send_audio(ws, audio))
        receiver = asyncio.create_task(collect())
        try:
            done, _ = await asyncio.wait([sender, receiver], return_when=asyncio.FIRST_COMPLETED)
            if receiver in done:
                await receiver  # Propagate receiver failures immediately.
                if not sender.done():
                    raise TranscriptionError("AssemblyAI terminated before all audio was sent.")
            await sender
            # Keep reading through the final SpeakerRevision and Termination.
            await asyncio.wait_for(receiver, timeout=40)
        finally:
            for task in (sender, receiver):
                if not task.done():
                    task.cancel()
            await asyncio.gather(sender, receiver, return_exceptions=True)
    return turns.result()


def write_result(path, result):
    # A failed session must never publish a partial transcript as a successful result.
    fd, temporary = tempfile.mkstemp(prefix=".transcript-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(result, output, ensure_ascii=False)
            output.write("\n")
        os.replace(temporary, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        api_key = os.environ.get("ASSEMBLYAI_API_KEY")
        if not api_key:
            raise TranscriptionError("ASSEMBLYAI_API_KEY is required.")
        audit_path = Path(str(args.output) + ".frames.jsonl")
        if args.audio.resolve() in (args.output.resolve(), audit_path.resolve()):
            raise TranscriptionError("Input audio and output paths must differ.")
        with wave.open(str(args.audio), "rb") as audio:
            if (audio.getnchannels() != 1 or audio.getsampwidth() != 2
                    or audio.getcomptype() != "NONE" or audio.getframerate() != 16000):
                raise TranscriptionError("Audio must be a 16 kHz mono, 16-bit PCM WAV.")
            if audio.getnframes() == 0:
                raise TranscriptionError("Audio is empty.")
            with audit_path.open("wb") as audit:
                result = asyncio.run(transcribe(audio, api_key, audit))
        write_result(args.output, result)
        print(f"Transcription complete: {len(result['turns'])} turns.", file=sys.stderr)
        return 0
    except KeyboardInterrupt:
        print("Transcription interrupted.", file=sys.stderr)
        return 130
    except TranscriptionError as exc:
        print(f"Transcription failed: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        # Avoid logging payloads, credentials, or potentially sensitive exception text.
        print(f"Transcription failed ({type(exc).__name__}); check paths, audio, network, API access, and frames audit log.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
