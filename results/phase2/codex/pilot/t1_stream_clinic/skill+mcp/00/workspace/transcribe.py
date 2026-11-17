"""Stream PCM WAV audio to AssemblyAI without receiving unredacted partials."""

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
PARAMETERS = {
    "sample_rate": 16000,
    "encoding": "pcm_s16le",
    "speech_model": MODEL,
    "domain": "medical-v1",
    "speaker_labels": "true",
    "speaker_labels_revision_interval_ms": 300000,
    "redact_pii": "true",
    # Omit policies to redact ALL supported types, rather than a limited subset.
    "redact_pii_sub": "entity_name",
    "include_partial_turns": "false",
    "min_turn_silence": 800,
    "max_turn_silence": 3600,
}


class TranscriptionError(Exception):
    pass


class Turns:
    def __init__(self):
        self.by_order = {}

    def accept(self, event):
        if event.get("type") == "Turn":
            if event.get("end_of_turn") is not True:
                raise TranscriptionError("Server returned a partial despite partials being disabled.")
            order = event["turn_order"]
            self.by_order[order] = {
                "speaker": event.get("speaker_label") or "UNKNOWN",
                "text": event["transcript"],
            }
        elif event.get("type") == "SpeakerRevision":
            for revision in event["revisions"]:
                order = revision["turn_order"]
                if order not in self.by_order:
                    raise TranscriptionError("Speaker revision references an unknown turn.")
                self.by_order[order]["speaker"] = revision.get("speaker_label") or "UNKNOWN"

    def result(self):
        return {"turns": [self.by_order[n] for n in sorted(self.by_order)]}


def validate_begin(event):
    config = event.get("configuration", {})
    expected = {"model": MODEL, "domain": "medical-v1",
                "speaker_labels": True, "redact_pii": True}
    for name, value in expected.items():
        if config.get(name) != value:
            raise TranscriptionError(f"Server did not confirm required configuration: {name}.")


async def send_audio(ws, audio):
    # Read PCM frames only, excluding the WAV header. Each 100ms chunk is
    # delivered after its capture duration, with no catch-up bursts on delays.
    while True:
        chunk = audio.readframes(1600)
        if not chunk:
            break
        if len(chunk) < 1600:  # Pad the last chunk to the API's 50ms minimum.
            chunk += b"\0" * (1600 - len(chunk))
        await asyncio.sleep(len(chunk) / 32000)
        await ws.send(chunk)
    await ws.send(json.dumps({"type": "Terminate"}))


async def transcribe(audio, audit, api_key):
    turns = Turns()
    url = "wss://streaming.assemblyai.com/v3/ws?" + urlencode(PARAMETERS)
    async with connect(url, additional_headers={"Authorization": api_key},
                       open_timeout=20, close_timeout=5, max_size=16 * 1024 * 1024) as ws:
        async def receive():
            raw = await asyncio.wait_for(ws.recv(), timeout=60)
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            # Preserve the server's exact JSON, including fields unknown to us.
            # No deserialize/reserialize step is used for the audit trail.
            audit.write(raw.encode("utf-8") + b"\n")
            audit.flush()
            event = json.loads(raw)
            if not isinstance(event, dict):
                raise TranscriptionError("Server returned a non-object message.")
            return event

        first = await receive()
        if first.get("type") != "Begin":
            raise TranscriptionError("Expected a Begin message; see audit log.")
        validate_begin(first)  # Fail before sending patient audio if misconfigured.
        sender = asyncio.create_task(send_audio(ws, audio))
        try:
            while True:
                receiver = asyncio.create_task(receive())
                try:
                    # A sender failure must not leave the receiver hanging.
                    if not sender.done():
                        await asyncio.wait({sender, receiver}, return_when=asyncio.FIRST_COMPLETED)
                    if sender.done():
                        await sender
                    event = await receiver
                finally:
                    if not receiver.done():
                        receiver.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await receiver
                kind = event.get("type")
                if kind == "Error" or event.get("error"):
                    raise TranscriptionError("AssemblyAI reported an error; see audit log.")
                if kind == "Termination":
                    if not sender.done():
                        raise TranscriptionError("Session terminated before all audio was sent.")
                    await sender
                    return turns.result()
                turns.accept(event)
        finally:
            sender.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await sender


def write_result(path, result):
    # A failed session never publishes a partial transcript as a successful file.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".transcript-", delete=False) as output:
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
    os.umask(0o077)
    api_key = os.environ.get("ASSEMBLYAI_API_KEY")
    if not api_key:
        raise TranscriptionError("ASSEMBLYAI_API_KEY must be set.")
    audit_path = Path(str(args.output) + ".frames.jsonl")
    if args.audio.resolve() in (args.output.resolve(), audit_path.resolve()):
        raise TranscriptionError("Output paths must not overwrite the audio input.")
    with wave.open(str(args.audio), "rb") as audio:
        if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate(),
                audio.getcomptype()) != (1, 2, 16000, "NONE"):
            raise TranscriptionError("Input must be an uncompressed 16-bit, 16 kHz mono WAV.")
        if audio.getnframes() == 0:
            raise TranscriptionError("Input WAV is empty.")
        with audit_path.open("wb") as audit:
            result = asyncio.run(transcribe(audio, audit, api_key))
    write_result(args.output, result)
    print(f"Saved {len(result['turns'])} final turns.", file=sys.stderr)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Transcription interrupted.", file=sys.stderr)
        sys.exit(130)
    except Exception as exc:
        # Exception bodies from network libraries may include response contents.
        # Avoid copying patient text or credentials into console logs.
        message = str(exc) if isinstance(exc, TranscriptionError) else type(exc).__name__
        print(f"Transcription failed: {message}", file=sys.stderr)
        sys.exit(1)
