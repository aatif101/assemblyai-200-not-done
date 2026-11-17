#!/usr/bin/env python3
"""Replay PCM WAV audio to AssemblyAI Streaming v3 without storing raw text."""

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


MODEL = "universal-3-6-pro"
ENDPOINT = "wss://streaming.assemblyai.com/v3/ws"
PARAMETERS = {
    "speech_model": MODEL,
    "sample_rate": 16000,
    "encoding": "pcm_s16le",
    "domain": "medical-v1",
    "speaker_labels": "true",
    "speaker_labels_revision_interval_ms": 300000,
    "redact_pii": "true",
    # Omit policies to redact ALL supported PII categories.
    "redact_pii_sub": "entity_name",
    "include_partial_turns": "false",
    # Explicit overrides apply even with diarization's own turn profile.
    # Allow pauses within a phone number or other multi-part entity.
    "min_turn_silence": 640,
    "max_turn_silence": 2560,
}


class TranscriptionError(Exception):
    pass


def check_begin(message):
    config = message.get("configuration", {})
    required = {
        "model": MODEL,
        "domain": "medical-v1",
        "speaker_labels": True,
        "redact_pii": True,
    }
    if any(config.get(key) != value for key, value in required.items()):
        raise TranscriptionError(
            "AssemblyAI did not confirm the requested model, Medical Mode, "
            "speaker labels and PII redaction; no audio was sent."
        )


class Turns:
    def __init__(self):
        self.turns = {}
        self.labels = {}

    def accept(self, message):
        kind = message.get("type")
        if kind == "Turn":
            if message.get("end_of_turn") is not True:
                raise TranscriptionError("Unexpected partial turn despite partials being disabled.")
            order = message["turn_order"]
            if not isinstance(order, int) or not isinstance(message.get("transcript"), str):
                raise TranscriptionError("Malformed final Turn message.")
            # A later final for this turn replaces it; never append partials.
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
                # Revisions update labels only. Keep the redacted final text.

    def result(self):
        return {"turns": [self.turns[key] for key in sorted(self.turns)]}


async def read_message(ws, audit):
    raw = await ws.recv()
    payload = raw.encode("utf-8") if isinstance(raw, str) else raw
    # Preserve the actual payload, including unknown fields and message types.
    # Do not reserialize through an SDK's typed event representation.
    audit.write(payload + b"\n")
    audit.flush()
    message = json.loads(payload)
    if not isinstance(message, dict):
        raise TranscriptionError("AssemblyAI returned a non-object JSON message.")
    if message.get("type") == "Error" or message.get("error"):
        # Avoid copying server-provided text into stderr.
        raise TranscriptionError("AssemblyAI reported an error; see the frames audit log.")
    return message


async def send_audio(ws, audio):
    """50ms binary PCM chunks; no WAV header, no catch-up bursts."""
    loop = asyncio.get_running_loop()
    deadline = loop.time()
    while True:
        chunk = audio.readframes(800)
        if not chunk:
            break
        # Pad only the last, short chunk to the service's 50ms minimum.
        chunk = chunk.ljust(1600, b"\0")
        await asyncio.sleep(max(0, deadline - loop.time()))
        await ws.send(chunk)
        deadline = max(deadline, loop.time()) + 0.05
    # Let the last chunk play out before asking for finalization.
    await asyncio.sleep(max(0, deadline - loop.time()))
    await ws.send('{"type":"Terminate"}')


async def receive_transcript(ws, audit, turns):
    while True:
        message = await read_message(ws, audit)
        turns.accept(message)
        if message.get("type") == "Termination":
            return


async def stream(audio, audit, api_key, endpoint=ENDPOINT):
    turns = Turns()
    async with connect(
        endpoint + "?" + urlencode(PARAMETERS),
        additional_headers={"Authorization": api_key},
        open_timeout=20,
        close_timeout=5,
        max_size=4 * 1024 * 1024,
    ) as ws:
        first = await asyncio.wait_for(read_message(ws, audit), timeout=20)
        if first.get("type") != "Begin":
            raise TranscriptionError("Expected Begin before streaming audio.")
        check_begin(first)
        sender = asyncio.create_task(send_audio(ws, audio))
        receiver = asyncio.create_task(receive_transcript(ws, audit, turns))
        try:
            # Detect server errors/closures while the file is still streaming.
            done, _ = await asyncio.wait({sender, receiver}, return_when=asyncio.FIRST_COMPLETED)
            if receiver in done:
                await receiver
                if not sender.done():
                    raise TranscriptionError("Session terminated before all audio was sent.")
            await sender
            # Termination is required: includes trailing text and speaker revisions.
            await asyncio.wait_for(receiver, timeout=60)
        finally:
            for task in (sender, receiver):
                if not task.done():
                    task.cancel()
            await asyncio.gather(sender, receiver, return_exceptions=True)
    return turns.result()


def write_result(path, result):
    # Publish only a complete transcript, using a private temporary file.
    descriptor, temporary = tempfile.mkstemp(prefix=".transcript-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(result, output, ensure_ascii=False)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        key = os.environ.get("ASSEMBLYAI_API_KEY")
        if not key:
            raise TranscriptionError("Set ASSEMBLYAI_API_KEY in your environment.")
        frames = Path(str(args.output) + ".frames.jsonl")
        if args.audio.resolve() in (args.output.resolve(), frames.resolve()):
            raise TranscriptionError("Output paths must not overwrite the input audio.")
        with wave.open(str(args.audio), "rb") as audio:
            if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate(), audio.getcomptype()) != (1, 2, 16000, "NONE"):
                raise TranscriptionError("Input must be a 16 kHz mono, 16-bit PCM WAV file.")
            if audio.getnframes() == 0:
                raise TranscriptionError("Input audio is empty.")
            with frames.open("wb") as audit:
                os.fchmod(audit.fileno(), 0o600)
                result = asyncio.run(stream(audio, audit, key))
                os.fsync(audit.fileno())
            write_result(args.output, result)
        return 0
    except KeyboardInterrupt:
        print("Transcription interrupted.", file=sys.stderr)
        return 130
    except Exception as error:
        if isinstance(error, TranscriptionError):
            print(str(error), file=sys.stderr)
        else:
            # Exception messages may contain server text or authentication data.
            print(f"Transcription failed ({type(error).__name__}); check input, output paths, network and audit log.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
