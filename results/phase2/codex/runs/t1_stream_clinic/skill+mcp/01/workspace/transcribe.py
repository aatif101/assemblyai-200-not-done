"""Stream PCM WAV audio without storing unredacted partial transcripts."""

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
PARAMS = {
    "speech_model": MODEL,
    "sample_rate": 16000,
    "encoding": "pcm_s16le",
    "domain": "medical-v1",
    "speaker_labels": "true",
    "speaker_labels_revision_interval_ms": 300000,
    "redact_pii": "true",
    # Omit policies to redact ALL supported PII types.
    "redact_pii_sub": "entity_name",
    "include_partial_turns": "false",
}


class TranscriptionError(Exception):
    pass


class Turns:
    def __init__(self):
        self.by_order = {}

    def accept(self, event):
        if event.get("type") == "Turn":
            if event.get("end_of_turn") is not True:
                raise TranscriptionError("Server unexpectedly returned a partial turn.")
            order = event["turn_order"]
            self.by_order[order] = {
                "speaker": event.get("speaker_label") or "PENDING",
                "text": event["transcript"],
            }
        elif event.get("type") == "SpeakerRevision":
            for revision in event["revisions"]:
                order = revision["turn_order"]
                if order not in self.by_order:
                    raise TranscriptionError("Speaker revision references an unknown turn.")
                # Retain the redacted transcript, never reconstruct it from words.
                self.by_order[order]["speaker"] = revision.get("speaker_label") or "PENDING"

    def result(self):
        return {"turns": [self.by_order[key] for key in sorted(self.by_order)]}


async def receive(ws, frames):
    raw = await ws.recv()
    # Preserve the original JSON payload, including whitespace and unknown fields.
    payload = raw.encode("utf-8") if isinstance(raw, str) else raw
    frames.write(payload + b"\n")
    frames.flush()
    event = json.loads(payload)
    if not isinstance(event, dict):
        raise TranscriptionError("Server returned a non-object message.")
    if event.get("type") == "Error" or "error" in event:
        raise TranscriptionError("AssemblyAI reported an error; inspect the frames log.")
    return event


def validate_begin(event):
    if event.get("type") != "Begin":
        raise TranscriptionError("Expected AssemblyAI Begin confirmation.")
    config = event.get("configuration", {})
    expected = {"model": MODEL, "domain": "medical-v1", "speaker_labels": True, "redact_pii": True}
    for key, value in expected.items():
        if config.get(key) != value:
            raise TranscriptionError(f"AssemblyAI did not confirm required setting: {key}.")
    if config.get("include_partial_turns") is True:
        raise TranscriptionError("AssemblyAI enabled unsafe partial turns.")


async def send_audio(ws, audio):
    # WAV headers aren't audio: wave.readframes returns only raw PCM samples.
    while chunk := audio.readframes(800):  # 50 ms at 16 kHz
        chunk = chunk.ljust(1600, b"\0")  # final frame must also be >= 50 ms
        start = asyncio.get_running_loop().time()
        await ws.send(chunk)
        # No catch-up bursts after scheduling or network delays.
        await asyncio.sleep(max(0, start + 0.05 - asyncio.get_running_loop().time()))
    await ws.send(json.dumps({"type": "Terminate"}))


async def collect(ws, frames, turns):
    while True:
        event = await receive(ws, frames)
        turns.accept(event)
        if event.get("type") == "Termination":
            return


async def stream(audio, frames, api_key):
    turns = Turns()
    async with connect(
        ENDPOINT + "?" + urlencode(PARAMS),
        additional_headers={"Authorization": api_key},
        open_timeout=20,
        close_timeout=5,
        max_size=8 * 1024 * 1024,
    ) as ws:
        validate_begin(await asyncio.wait_for(receive(ws, frames), timeout=30))
        sender = asyncio.create_task(send_audio(ws, audio))
        receiver = asyncio.create_task(collect(ws, frames, turns))
        try:
            done, _ = await asyncio.wait([sender, receiver], return_when=asyncio.FIRST_COMPLETED)
            if receiver in done:
                receiver.result()
                if not sender.done():
                    raise TranscriptionError("AssemblyAI terminated before all audio was sent.")
            await sender
            await asyncio.wait_for(receiver, timeout=60)
        finally:
            for task in (sender, receiver):
                if not task.done():
                    task.cancel()
            await asyncio.gather(sender, receiver, return_exceptions=True)
    return turns.result()


def write_result(path, result):
    # Publish only a complete successful result, with restricted permissions.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as output:
            temporary = output.name
            json.dump(result, output, ensure_ascii=False)
            output.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    try:
        api_key = os.environ.get("ASSEMBLYAI_API_KEY")
        if not api_key:
            raise TranscriptionError("Set ASSEMBLYAI_API_KEY in your environment.")
        frames_path = Path(str(args.output) + ".frames.jsonl")
        if args.audio.resolve() in (args.output.resolve(), frames_path.resolve()):
            raise TranscriptionError("Output paths must differ from the input audio.")
        with wave.open(str(args.audio), "rb") as audio:
            if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate(), audio.getcomptype()) != (1, 2, 16000, "NONE"):
                raise TranscriptionError("Audio must be a 16 kHz mono 16-bit PCM WAV.")
            if not audio.getnframes():
                raise TranscriptionError("Audio is empty.")
            descriptor = os.open(frames_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(descriptor, "wb") as frames:
                os.fchmod(frames.fileno(), 0o600)
                result = asyncio.run(stream(audio, frames, api_key))
        write_result(args.output, result)
        print(f"Transcribed {len(result['turns'])} turns.", file=sys.stderr)
        return 0
    except KeyboardInterrupt:
        print("Transcription interrupted.", file=sys.stderr)
        return 130
    except Exception as exc:
        # Never print server payloads, transcript text, keys, or network headers.
        message = str(exc) if isinstance(exc, TranscriptionError) else type(exc).__name__
        print(f"Transcription failed: {message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
