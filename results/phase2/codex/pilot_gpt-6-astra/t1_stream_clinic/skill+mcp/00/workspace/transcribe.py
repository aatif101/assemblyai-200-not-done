"""Replay WAV audio to AssemblyAI v3, preserving received JSON messages."""
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
PARAMS = {
    "speech_model": MODEL,
    "sample_rate": 16000,
    "encoding": "pcm_s16le",
    "domain": "medical-v1",
    "speaker_labels": "true",
    "speaker_labels_revision_interval_ms": 300000,
    "redact_pii": "true",
    # Omitting policies selects ALL supported PII categories.
    "redact_pii_sub": "entity_name",
    "include_partial_turns": "false",
    # Explicit overrides survive the diarization turn-detection profile.
    "min_turn_silence": 1000,
    "max_turn_silence": 3600,
}
URL = "wss://streaming.assemblyai.com/v3/ws?" + urlencode(PARAMS)


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
                raise TranscriptionError("Server returned a partial despite partials being disabled.")
            order = message["turn_order"]
            text = message["transcript"]
            if not isinstance(order, int) or not isinstance(text, str):
                raise TranscriptionError("Invalid turn structure.")
            self.turns[order] = {
                "speaker": self.labels.get(order, message.get("speaker_label")) or "UNKNOWN",
                "text": text,
            }
        elif kind == "SpeakerRevision":
            for revision in message["revisions"]:
                order = revision["turn_order"]
                label = revision.get("speaker_label") or "UNKNOWN"
                self.labels[order] = label
                if order in self.turns:
                    self.turns[order]["speaker"] = label
        elif kind == "Error" or message.get("error"):
            # Server error text may contain sensitive data; it is in the audit log.
            raise TranscriptionError("AssemblyAI reported an error; session failed.")

    def result(self):
        return {"turns": [self.turns[k] for k in sorted(self.turns)]}


async def receive(ws, audit):
    raw = await asyncio.wait_for(ws.recv(), timeout=60)
    data = raw.encode("utf-8") if isinstance(raw, str) else raw
    # Preserve the actual payload, including whitespace and unknown fields.
    audit.write(data + b"\n")
    audit.flush()
    message = json.loads(data)
    if not isinstance(message, dict):
        raise TranscriptionError("Expected a JSON object from AssemblyAI.")
    return message


def validate_begin(message):
    if message.get("type") != "Begin":
        raise TranscriptionError("AssemblyAI did not begin the session.")
    config = message.get("configuration") or {}
    expected = {"model": MODEL, "domain": "medical-v1", "speaker_labels": True, "redact_pii": True}
    if any(config.get(key) != value for key, value in expected.items()):
        raise TranscriptionError("Server did not confirm model, medical mode, speakers and PII redaction; no audio sent.")


async def send_audio(ws, source):
    # 800 samples = 50 ms. Pad only the last short chunk with silence.
    # Wait before sending each chunk, as if its samples were being captured.
    remaining = source.getnframes()
    while remaining:
        count = min(800, remaining)
        chunk = source.readframes(count)
        if len(chunk) != count * 2:
            raise TranscriptionError("WAV is truncated.")
        remaining -= count
        await asyncio.sleep(0.05)
        await ws.send(chunk.ljust(1600, b"\x00"))
    await ws.send(json.dumps({"type": "Terminate"}))


async def collect(ws, audit, turns):
    while True:
        message = await receive(ws, audit)
        turns.accept(message)
        if message.get("type") == "Termination":
            return


async def transcribe(source, audit, api_key):
    turns = Turns()
    async with connect(
        URL, additional_headers={"Authorization": api_key},
        open_timeout=20, close_timeout=5, max_size=8 * 1024 * 1024,
    ) as ws:
        validate_begin(await receive(ws, audit))
        sender = asyncio.create_task(send_audio(ws, source))
        receiver = asyncio.create_task(collect(ws, audit, turns))
        try:
            done, _ = await asyncio.wait({sender, receiver}, return_when=asyncio.FIRST_COMPLETED)
            if receiver in done:
                receiver.result()
                if not sender.done():
                    raise TranscriptionError("Session terminated before all audio was sent.")
            await sender
            await asyncio.wait_for(receiver, timeout=60)
        finally:
            for task in (sender, receiver):
                if not task.done():
                    task.cancel()
            await asyncio.gather(sender, receiver, return_exceptions=True)
    return turns.result()


def write_result(path, result):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as f:
            temporary = f.name
            json.dump(result, f, ensure_ascii=False)
            f.write("\n")
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
    os.umask(0o077)
    try:
        api_key = os.environ.get("ASSEMBLYAI_API_KEY")
        if not api_key:
            raise TranscriptionError("Set ASSEMBLYAI_API_KEY in the environment.")
        audit_path = Path(str(args.output) + ".frames.jsonl")
        if args.output.exists() or audit_path.exists():
            raise TranscriptionError("Output or audit file already exists; choose a new output path.")
        with wave.open(str(args.audio), "rb") as source:
            if (source.getnchannels(), source.getsampwidth(), source.getframerate(), source.getcomptype()) != (1, 2, 16000, "NONE"):
                raise TranscriptionError("Input must be 16 kHz mono 16-bit PCM WAV.")
            if source.getnframes() == 0:
                raise TranscriptionError("Input WAV is empty.")
            with audit_path.open("xb") as audit:
                result = asyncio.run(transcribe(source, audit, api_key))
            write_result(args.output, result)
        print(f"Completed: {len(result['turns'])} turns.", file=sys.stderr)
        return 0
    except TranscriptionError as exc:
        print(f"Error: {exc}", file=sys.stderr)
    except KeyboardInterrupt:
        print("Error: interrupted.", file=sys.stderr)
    except Exception as exc:
        # Never print raw provider payloads, credentials or transcript text.
        print(f"Error: {type(exc).__name__}; transcription did not complete.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
