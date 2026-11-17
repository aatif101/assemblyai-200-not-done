#!/usr/bin/env python3
"""Replay PCM WAV audio through AssemblyAI's redacted streaming API."""

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
    # Omit policies deliberately: AssemblyAI's default covers all PII types.
    "redact_pii_sub": "entity_name",
    "include_partial_turns": "false",
    # Explicit overrides apply even with the diarization turn-detection profile.
    # Longer pauses help keep dictated identifiers together for redaction.
    "min_turn_silence": 1000,
    "max_turn_silence": 2560,
    "session_heartbeat": "true",
}


class TranscriptionError(Exception):
    """An error whose message is safe to display without transcript content."""


def check_configuration(frame):
    config = frame.get("configuration", {})
    expected = {
        "model": MODEL,
        "domain": "medical-v1",
        "speaker_labels": True,
        "redact_pii": True,
    }
    for field, value in expected.items():
        if config.get(field) != value:
            raise TranscriptionError(f"AssemblyAI did not confirm required setting: {field}")


class Turns:
    def __init__(self):
        self.turns = {}
        self.labels = {}

    def accept(self, frame):
        kind = frame.get("type")
        if kind == "Turn":
            if frame.get("end_of_turn") is not True:
                raise TranscriptionError("AssemblyAI returned a partial despite partials being disabled")
            order = frame["turn_order"]
            # Replace repeated final/formatted events rather than duplicating text.
            self.turns[order] = {
                "speaker": self.labels.get(order, frame.get("speaker_label")) or "UNKNOWN",
                "text": frame["transcript"],
            }
        elif kind == "SpeakerRevision":
            for revision in frame["revisions"]:
                order = revision["turn_order"]
                label = revision.get("speaker_label") or "UNKNOWN"
                self.labels[order] = label
                if order in self.turns:
                    self.turns[order]["speaker"] = label

    def result(self):
        return {"turns": [self.turns[order] for order in sorted(self.turns)]}


async def receive(ws, audit, begun, turns):
    while True:
        raw = await asyncio.wait_for(ws.recv(), timeout=30)
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        # Preserve the received JSON itself, including unknown fields and spacing.
        # Never reconstruct audit frames from SDK models or parsed dictionaries.
        audit.write(raw + "\n")
        audit.flush()
        frame = json.loads(raw)
        kind = frame.get("type")
        if kind == "Begin":
            check_configuration(frame)
            begun.set()
        elif kind == "Error" or "error" in frame:
            raise TranscriptionError("AssemblyAI reported a streaming error; see the audit log")
        elif kind == "Termination":
            return
        else:
            turns.accept(frame)


async def send_audio(ws, audio):
    # Strip the WAV container: the socket expects little-endian signed PCM.
    # Never catch up after delays by bursting queued audio faster than realtime.
    loop = asyncio.get_running_loop()
    while True:
        data = audio.readframes(800)  # 50ms at 16kHz, 16-bit mono.
        if not data:
            break
        if len(data) < 1600:
            data += b"\x00" * (1600 - len(data))
        sent_at = loop.time()
        await ws.send(data)
        await asyncio.sleep(max(0, sent_at + 0.05 - loop.time()))
    # Terminate flushes the last turn and requests final speaker revisions.
    await ws.send(json.dumps({"type": "Terminate"}))


async def transcribe(audio, audit, api_key):
    turns = Turns()
    async with connect(
        ENDPOINT + "?" + urlencode(PARAMS),
        additional_headers={"Authorization": api_key},
        open_timeout=20,
        close_timeout=5,
        max_size=8 * 1024 * 1024,
    ) as ws:
        begun = asyncio.Event()
        receiver = asyncio.create_task(receive(ws, audit, begun, turns))
        begin_waiter = asyncio.create_task(begun.wait())
        sender = None
        try:
            done, _ = await asyncio.wait(
                [receiver, begin_waiter], timeout=20, return_when=asyncio.FIRST_COMPLETED
            )
            if receiver in done:
                await receiver
                raise TranscriptionError("AssemblyAI terminated before audio was sent")
            if begin_waiter not in done:
                raise TranscriptionError("Timed out waiting for AssemblyAI Begin")
            sender = asyncio.create_task(send_audio(ws, audio))
            done, _ = await asyncio.wait([sender, receiver], return_when=asyncio.FIRST_COMPLETED)
            if receiver in done:
                await receiver
                if not sender.done():
                    raise TranscriptionError("AssemblyAI terminated before audio finished")
            await sender
            await asyncio.wait_for(receiver, timeout=60)
        finally:
            for task in (begin_waiter, sender, receiver):
                if task is not None:
                    task.cancel()
            await asyncio.gather(
                *(t for t in (begin_waiter, sender, receiver) if t is not None),
                return_exceptions=True,
            )
    return turns.result()


def write_result(output, result):
    # A failed run never publishes a partial transcript as a successful result.
    fd, temporary = tempfile.mkstemp(prefix=".transcript-", dir=output.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(result, stream, ensure_ascii=False)
            stream.write("\n")
        os.replace(temporary, output)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    api_key = os.environ.get("ASSEMBLYAI_API_KEY")
    if not api_key:
        raise TranscriptionError("Set ASSEMBLYAI_API_KEY in the environment")
    audit_path = Path(str(args.output) + ".frames.jsonl")
    if args.audio.resolve() in (args.output.resolve(), audit_path.resolve()):
        raise TranscriptionError("Output paths must differ from the input audio")
    with wave.open(str(args.audio), "rb") as audio:
        if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate(), audio.getcomptype()) != (1, 2, 16000, "NONE"):
            raise TranscriptionError("Input must be a 16kHz mono signed 16-bit PCM WAV")
        if audio.getnframes() == 0:
            raise TranscriptionError("Input audio is empty")
        fd = os.open(audit_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as audit:
            result = asyncio.run(transcribe(audio, audit, api_key))
    write_result(args.output, result)


if __name__ == "__main__":
    try:
        main()
    except TranscriptionError as exc:
        print(f"Transcription failed: {exc}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("Transcription interrupted", file=sys.stderr)
        sys.exit(130)
    except Exception as exc:
        # Library exceptions can contain server text; keep PII out of terminal logs.
        print(f"Transcription failed ({type(exc).__name__})", file=sys.stderr)
        sys.exit(1)
