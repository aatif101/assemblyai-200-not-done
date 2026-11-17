#!/usr/bin/env python3
"""Paced WAV -> AssemblyAI v3, retaining raw server messages and final turns."""
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
PARAMS = {
    "speech_model": MODEL,
    "sample_rate": 16000,
    "encoding": "pcm_s16le",
    "domain": "medical-v1",
    "speaker_labels": "true",
    "speaker_labels_revision_interval_ms": 300000,
    "redact_pii": "true",
    # Omit policies to enable ALL supported PII categories.
    "redact_pii_sub": "entity_name",
    "include_partial_turns": "false",
    # Explicit silence settings also apply with the diarization profile.
    "min_turn_silence": 640,
    "max_turn_silence": 2560,
    "session_heartbeat": "true",
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
                raise TranscriptionError("Unexpected partial turn despite partials being disabled.")
            order = message["turn_order"]
            text = message["transcript"]
            if not isinstance(order, int) or not isinstance(text, str):
                raise TranscriptionError("Invalid final turn fields.")
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

    def result(self):
        return {"turns": [self.turns[k] for k in sorted(self.turns)]}


async def receive(ws, audit):
    raw = await asyncio.wait_for(ws.recv(), timeout=30)
    # Keep the received payload intact, including fields unknown to this client.
    payload = raw.encode("utf-8") if isinstance(raw, str) else raw
    audit.write(payload + b"\n")
    audit.flush()
    message = json.loads(payload)
    if not isinstance(message, dict):
        raise TranscriptionError("Server sent a non-object message.")
    if message.get("type") == "Error" or message.get("error"):
        # The original error is in the audit. Never echo server transcript data.
        raise TranscriptionError("AssemblyAI returned an error; inspect the frames audit.")
    return message


def verify_begin(message):
    if message.get("type") != "Begin":
        raise TranscriptionError("Expected AssemblyAI Begin message.")
    config = message.get("configuration", {})
    expected = {"model": MODEL, "domain": "medical-v1", "speaker_labels": True, "redact_pii": True}
    if any(config.get(k) != v for k, v in expected.items()):
        raise TranscriptionError("AssemblyAI did not confirm the required model, medical mode, diarization and PII redaction. No audio sent.")


async def send_audio(ws, audio):
    loop = asyncio.get_running_loop()
    deadline = loop.time()
    while True:
        chunk = audio.readframes(800)  # 50 ms, 16 kHz signed 16-bit mono, no WAV header.
        if not chunk:
            break
        # Pad only the last short chunk to the server's minimum of 50 ms.
        chunk = chunk.ljust(1600, b"\0")
        # Never catch up with a burst after a scheduling/network delay.
        deadline = max(deadline, loop.time()) + 0.05
        await asyncio.sleep(max(0, deadline - loop.time()))
        await asyncio.wait_for(ws.send(chunk), timeout=30)
    await ws.send(json.dumps({"type": "Terminate"}))


async def collect(ws, audit, turns):
    while True:
        message = await receive(ws, audit)
        turns.accept(message)
        if message.get("type") == "Termination":
            return


async def transcribe(audio, audit, key):
    url = "wss://streaming.assemblyai.com/v3/ws?" + urlencode(PARAMS)
    turns = Turns()
    async with connect(url, additional_headers={"Authorization": key},
                       open_timeout=30, close_timeout=5, max_size=4 * 1024 * 1024) as ws:
        verify_begin(await receive(ws, audit))
        sender = asyncio.create_task(send_audio(ws, audio))
        receiver = asyncio.create_task(collect(ws, audit, turns))
        try:
            done, _ = await asyncio.wait({sender, receiver}, return_when=asyncio.FIRST_COMPLETED)
            if receiver in done:
                await receiver  # Propagate errors/early socket closure.
                if not sender.done():
                    raise TranscriptionError("Session terminated before all audio was sent.")
            await sender
            # This includes the last turn and the final speaker revision pass.
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
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".transcript-", delete=False) as stream:
            temporary = stream.name
            json.dump(result, stream, ensure_ascii=False)
            stream.write("\n")
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
        key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
        if not key:
            raise TranscriptionError("ASSEMBLYAI_API_KEY is not set.")
        frames = Path(str(args.output) + ".frames.jsonl")
        if len({args.audio.resolve(), args.output.resolve(), frames.resolve()}) != 3:
            raise TranscriptionError("Input, output, and audit paths must be different.")
        with wave.open(str(args.audio), "rb") as audio:
            if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate(), audio.getcomptype()) != (1, 2, 16000, "NONE"):
                raise TranscriptionError("Input must be uncompressed 16 kHz mono 16-bit PCM WAV.")
            if audio.getnframes() == 0:
                raise TranscriptionError("Input WAV is empty.")
            if audio.getnframes() / 16000 > 10700:
                raise TranscriptionError("Input exceeds the supported single-session duration.")
            fd = os.open(frames, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "wb") as audit:
                os.fchmod(audit.fileno(), 0o600)
                result = asyncio.run(transcribe(audio, audit, key))
        write_result(args.output, result)
        print(f"Transcription complete: {len(result['turns'])} turns.", file=sys.stderr)
        return 0
    except KeyboardInterrupt:
        print("Transcription interrupted.", file=sys.stderr)
        return 130
    except Exception as error:
        # Avoid printing arbitrary exception payloads, which may include server text.
        detail = str(error) if isinstance(error, TranscriptionError) else type(error).__name__
        print(f"Transcription failed: {detail}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
