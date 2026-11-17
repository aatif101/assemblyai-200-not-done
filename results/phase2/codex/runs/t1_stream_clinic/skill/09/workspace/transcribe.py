#!/usr/bin/env python3
"""Replay PCM WAV audio to AssemblyAI streaming v3 without logging partial text."""
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
    # Omitting policies selects ALL supported PII policies.
    "redact_pii_sub": "entity_name",
    "include_partial_turns": "false",
    "session_heartbeat": "true",
}


class TranscriptionError(Exception):
    pass


async def read_message(ws, audit):
    raw = await asyncio.wait_for(ws.recv(), timeout=60)
    if not isinstance(raw, str):
        raise TranscriptionError("Unexpected binary message from server")
    # Preserve the original message bytes, including unknown fields and spacing.
    audit.write(raw.encode("utf-8") + b"\n")
    audit.flush()
    message = json.loads(raw)
    if not isinstance(message, dict):
        raise TranscriptionError("Expected a JSON object from server")
    if message.get("type") == "Error" or "error" in message:
        # Never print server text: errors could include sensitive transcript data.
        raise TranscriptionError("AssemblyAI reported an error; see the audit log")
    return message


def verify_begin(message):
    if message.get("type") != "Begin":
        raise TranscriptionError("Expected Begin before streaming audio")
    config = message.get("configuration", {})
    required = {"model": MODEL, "domain": "medical-v1",
                "speaker_labels": True, "redact_pii": True}
    for name, expected in required.items():
        if config.get(name) != expected:
            raise TranscriptionError(f"Server did not enable required setting: {name}")


async def send_audio(ws, audio):
    # Wait for each chunk's duration BEFORE sending, as a live capture would.
    # Reset the deadline after a stalled send to avoid catch-up bursts.
    loop = asyncio.get_running_loop()
    deadline = loop.time()
    while True:
        chunk = audio.readframes(800)  # 50ms, 16-bit mono PCM; no WAV header.
        if not chunk:
            break
        if len(chunk) < 1600:
            chunk += b"\0" * (1600 - len(chunk))
        deadline = max(deadline, loop.time()) + 0.05
        await asyncio.sleep(max(0, deadline - loop.time()))
        await ws.send(chunk)
    # Terminate flushes the last turn and triggers final speaker revisions.
    await ws.send('{"type":"Terminate"}')


async def receive_turns(ws, audit, turns):
    while True:
        message = await read_message(ws, audit)
        kind = message.get("type")
        if kind == "Turn":
            if message.get("end_of_turn") is not True:
                raise TranscriptionError("Server emitted a partial despite partials being disabled")
            order = message["turn_order"]
            text = message["transcript"]
            if not isinstance(order, int) or not isinstance(text, str):
                raise TranscriptionError("Invalid final turn")
            turns[order] = {"speaker": message.get("speaker_label") or "UNKNOWN", "text": text}
        elif kind == "SpeakerRevision":
            for revision in message["revisions"]:
                order = revision["turn_order"]
                if order not in turns:
                    raise TranscriptionError("Speaker revision references an unknown turn")
                turns[order]["speaker"] = revision.get("speaker_label") or "UNKNOWN"
        elif kind == "Termination":
            return


async def stream(audio, audit, key):
    turns = {}
    async with connect(ENDPOINT + "?" + urlencode(PARAMS),
                       additional_headers={"Authorization": key},
                       open_timeout=20, close_timeout=5, max_size=4 * 1024 * 1024) as ws:
        verify_begin(await read_message(ws, audit))
        sender = asyncio.create_task(send_audio(ws, audio))
        receiver = asyncio.create_task(receive_turns(ws, audit, turns))
        try:
            done, _ = await asyncio.wait((sender, receiver), return_when=asyncio.FIRST_COMPLETED)
            if receiver in done:
                await receiver  # Propagate a server error or premature close.
                if not sender.done():
                    raise TranscriptionError("Session terminated before all audio was sent")
            await sender
            await asyncio.wait_for(receiver, timeout=60)
        finally:
            for task in (sender, receiver):
                if not task.done():
                    task.cancel()
            await asyncio.gather(sender, receiver, return_exceptions=True)
    return {"turns": [turns[order] for order in sorted(turns)]}


def write_result(path, result):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".transcript-", delete=False) as output:
            temporary = output.name
            json.dump(result, output, ensure_ascii=False)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
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
        key = os.environ.get("ASSEMBLYAI_API_KEY")
        if not key:
            raise TranscriptionError("ASSEMBLYAI_API_KEY is required")
        audit_path = Path(str(args.output) + ".frames.jsonl")
        paths = [args.audio.resolve(), args.output.resolve(), audit_path.resolve()]
        if len(set(paths)) != 3:
            raise TranscriptionError("Input, output, and audit paths must be different")
        with wave.open(str(args.audio), "rb") as audio:
            if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate(), audio.getcomptype()) != (1, 2, 16000, "NONE"):
                raise TranscriptionError("Audio must be 16 kHz mono 16-bit uncompressed PCM WAV")
            if not audio.getnframes():
                raise TranscriptionError("Audio is empty")
            # Clear stale success output; failed sessions retain their audit frames.
            args.output.unlink(missing_ok=True)
            fd = os.open(audit_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "wb") as audit:
                result = asyncio.run(stream(audio, audit, key))
            write_result(args.output, result)
        return 0
    except (Exception, KeyboardInterrupt) as exc:
        if isinstance(exc, TranscriptionError):
            print(f"Transcription failed: {exc}", file=sys.stderr)
        else:
            # Do not echo arbitrary exceptions that might contain credentials/PII.
            print(f"Transcription failed ({type(exc).__name__})", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
