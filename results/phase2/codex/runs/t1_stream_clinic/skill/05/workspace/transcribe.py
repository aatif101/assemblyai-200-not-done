#!/usr/bin/env python3
"""Paced WAV replay into AssemblyAI v3; persist server-redacted finals only."""
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


PARAMS = {
    "speech_model": "universal-3-6-pro",
    "sample_rate": 16000,
    "encoding": "pcm_s16le",
    "domain": "medical-v1",
    "speaker_labels": "true",
    "speaker_labels_revision_interval_ms": 300000,
    "redact_pii": "true",
    # Omit policies to redact ALL supported entity types.
    "redact_pii_sub": "entity_name",
    "include_partial_turns": "false",
    # Explicit overrides: diarization otherwise overrides the mode preset.
    "min_turn_silence": 512,
    "max_turn_silence": 2560,
}
URL = "wss://streaming.assemblyai.com/v3/ws?" + urlencode(PARAMS)


class TranscriptionError(Exception):
    pass


def check_begin(message):
    expected = {
        "model": PARAMS["speech_model"],
        "domain": "medical-v1",
        "speaker_labels": True,
        "redact_pii": True,
    }
    config = message.get("configuration", {})
    if any(config.get(key) != value for key, value in expected.items()):
        raise TranscriptionError("Server did not confirm the required model, medical mode, speaker labels and PII redaction; no audio sent.")


class Turns:
    def __init__(self):
        self.turns = {}
        self.labels = {}

    def accept(self, message):
        if message.get("type") == "Turn":
            if message.get("end_of_turn") is not True:
                raise TranscriptionError("Server sent a partial despite partials being disabled.")
            order = message["turn_order"]
            text = message.get("utterance") or message["transcript"]
            self.turns[order] = {"speaker": message.get("speaker_label"), "text": text}
        elif message.get("type") == "SpeakerRevision":
            for revision in message["revisions"]:
                self.labels[revision["turn_order"]] = revision.get("speaker_label")

    def result(self):
        return {"turns": [
            {"speaker": self.labels.get(order, turn["speaker"]) or "UNKNOWN", "text": turn["text"]}
            for order, turn in sorted(self.turns.items())
        ]}


async def receive(ws, audit, turns, begun, terminated):
    async for raw in ws:
        if not isinstance(raw, str):
            raise TranscriptionError("Unexpected binary server message.")
        # Save the exact received JSON text, not an SDK projection or a reserialization.
        # PII redaction and partial suppression are performed by the server.
        audit.write(raw.encode("utf-8") + b"\n")
        audit.flush()
        message = json.loads(raw)
        kind = message.get("type")
        if kind == "Begin":
            check_begin(message)
            begun.set()
        elif kind == "Error" or message.get("error"):
            # Do not print arbitrary server payloads that could contain private text.
            raise TranscriptionError("AssemblyAI reported an error; see the audit log.")
        elif kind == "Termination":
            terminated.set()
            return
        else:
            turns.accept(message)
    raise TranscriptionError("Connection closed before Termination confirmation.")


async def replay(ws, wav):
    # 800 samples = 50 ms. Pad only a final short packet to the API minimum.
    # Pace relative to each send, so a delayed send never causes a catch-up burst.
    loop = asyncio.get_running_loop()
    while chunk := wav.readframes(800):
        chunk = chunk.ljust(1600, b"\0")
        deadline = loop.time() + 0.05
        await ws.send(chunk)
        await asyncio.sleep(max(0, deadline - loop.time()))
    # Terminate flushes pending final text and emits final speaker revisions.
    await ws.send(json.dumps({"type": "Terminate"}))


async def session(wav, audit, key):
    turns = Turns()
    begun, terminated = asyncio.Event(), asyncio.Event()
    async with connect(URL, additional_headers={"Authorization": key},
                       open_timeout=20, close_timeout=5, max_size=8 * 1024 * 1024) as ws:
        reader = asyncio.create_task(receive(ws, audit, turns, begun, terminated))
        begin_wait = asyncio.create_task(begun.wait())
        sender = None
        try:
            done, _ = await asyncio.wait({reader, begin_wait}, timeout=20,
                                         return_when=asyncio.FIRST_COMPLETED)
            if reader in done:
                await reader
                raise TranscriptionError("Session terminated before audio was sent.")
            if begin_wait not in done:
                raise TranscriptionError("Timed out waiting for Begin.")
            sender = asyncio.create_task(replay(ws, wav))
            done, _ = await asyncio.wait({reader, sender}, return_when=asyncio.FIRST_COMPLETED)
            if reader in done:
                await reader
                if not sender.done():
                    raise TranscriptionError("Session terminated before all audio was sent.")
            await sender
            await asyncio.wait_for(reader, timeout=45)
            if not terminated.is_set():
                raise TranscriptionError("Missing Termination confirmation.")
        finally:
            for task in (reader, begin_wait, sender):
                if task is not None:
                    task.cancel()
            for task in (reader, begin_wait, sender):
                if task is not None:
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await task
    return turns.result()


def write_result(output, result):
    # Publish a complete result atomically, only after successful termination.
    fd, temporary = tempfile.mkstemp(prefix=".transcript-", dir=output.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(result, stream, ensure_ascii=False)
            stream.write("\n")
        os.replace(temporary, output)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main():
    os.umask(0o077)
    if len(sys.argv) != 3:
        print("Usage: ./run.sh <audio.wav> <out.json>", file=sys.stderr)
        return 2
    key = os.environ.get("ASSEMBLYAI_API_KEY")
    if not key:
        print("ASSEMBLYAI_API_KEY must be set.", file=sys.stderr)
        return 2
    source, output = map(Path, sys.argv[1:])
    audit_path = Path(str(output) + ".frames.jsonl")
    try:
        if source.resolve() in {output.resolve(), audit_path.resolve()}:
            raise TranscriptionError("Input and output paths must differ.")
        with wave.open(str(source), "rb") as wav:
            if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getcomptype()) != (1, 2, 16000, "NONE"):
                raise TranscriptionError("Audio must be uncompressed 16-bit PCM, 16 kHz, mono WAV.")
            if wav.getnframes() == 0:
                raise TranscriptionError("Audio file is empty.")
            with audit_path.open("wb") as audit:
                os.chmod(audit_path, 0o600)
                result = asyncio.run(session(wav, audit, key))
        write_result(output, result)
    except KeyboardInterrupt:
        print("Transcription interrupted.", file=sys.stderr)
        return 130
    except TranscriptionError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Transcription failed ({type(exc).__name__}). No new final transcript was written.", file=sys.stderr)
        return 1
    print(f"Wrote {len(result['turns'])} final turns to {output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
