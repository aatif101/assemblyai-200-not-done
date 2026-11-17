#!/usr/bin/env python3
"""Paced WAV -> AssemblyAI v3; preserve wire messages and collect final turns."""

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


def connection_url():
    return ENDPOINT + "?" + urlencode({
        "sample_rate": 16000,
        "encoding": "pcm_s16le",
        "speech_model": MODEL,
        "domain": "medical-v1",
        "speaker_labels": "true",
        "redact_pii": "true",
        # Omit policies to redact ALL supported PII types.
        "redact_pii_sub": "entity_name",
        "include_partial_turns": "false",
        "min_turn_silence": 1000,
        "max_turn_silence": 3600,
    })


def validate_begin(message):
    config = message.get("configuration", {})
    expected = {"model": MODEL, "domain": "medical-v1",
                "speaker_labels": True, "redact_pii": True}
    if any(config.get(key) != value for key, value in expected.items()):
        raise RuntimeError("AssemblyAI did not confirm the requested model, medical mode, speaker labels and PII redaction")
    if config.get("include_partial_turns") is True:
        raise RuntimeError("AssemblyAI enabled unredacted partial turns")


class Turns:
    def __init__(self):
        self.finals = {}
        self.speakers = {}

    def accept(self, message):
        kind = message.get("type")
        if kind == "Turn":
            if not message.get("end_of_turn"):
                raise RuntimeError("Unexpected partial turn despite disabled partials")
            order = message["turn_order"]
            self.finals[order] = message["transcript"]
            self.speakers[order] = message.get("speaker_label")
        elif kind == "SpeakerRevision":
            for revision in message["revisions"]:
                self.speakers[revision["turn_order"]] = revision.get("speaker_label")

    def result(self):
        return {"turns": [
            {"speaker": self.speakers.get(order) or "UNKNOWN", "text": text}
            for order, text in sorted(self.finals.items()) if text.strip()
        ]}


async def send_audio(ws, audio, sent_all):
    # Each packet is 50ms of PCM, without the WAV container header.
    # Sleep after each packet, so a slow connection never causes catch-up bursts.
    while True:
        data = audio.readframes(800)
        if not data:
            break
        data = data.ljust(1600, b"\0")  # Last packet must also be >=50ms.
        start = asyncio.get_running_loop().time()
        await ws.send(data)
        await asyncio.sleep(max(0, 0.05 - (asyncio.get_running_loop().time() - start)))
    await ws.send(json.dumps({"type": "Terminate"}))
    sent_all.set()


async def receive(ws, audit, turns, begun, terminated, sent_all):
    async for raw in ws:
        # Preserve exactly what the server sent, including unknown fields/types.
        # Never serialize SDK event objects or print transcript text to stderr.
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        audit.write(raw + "\n")
        audit.flush()
        message = json.loads(raw)
        if not isinstance(message, dict):
            raise RuntimeError("AssemblyAI sent a non-object JSON message")
        kind = message.get("type")
        if kind == "Begin":
            validate_begin(message)
            begun.set()
        elif kind == "Error" or message.get("error"):
            raise RuntimeError("AssemblyAI reported an error; see the frames log")
        elif kind == "Termination":
            if not sent_all.is_set():
                raise RuntimeError("AssemblyAI terminated before all audio was sent")
            terminated.set()
            return
        else:
            turns.accept(message)
    raise RuntimeError("AssemblyAI closed without a Termination message")


async def run(audio_path, output_path):
    key = os.environ.get("ASSEMBLYAI_API_KEY")
    if not key:
        raise RuntimeError("ASSEMBLYAI_API_KEY is required")
    output = Path(output_path)
    frames = Path(str(output) + ".frames.jsonl")
    if Path(audio_path).resolve() in (output.resolve(), frames.resolve()):
        raise RuntimeError("Output paths must differ from the input audio")
    turns = Turns()
    with wave.open(str(audio_path), "rb") as audio:
        if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate(), audio.getcomptype()) != (1, 2, 16000, "NONE"):
            raise RuntimeError("Input must be a 16 kHz mono 16-bit PCM WAV")
        if not audio.getnframes():
            raise RuntimeError("Input audio is empty")
        with frames.open("w", encoding="utf-8", newline="\n") as audit:
            async with connect(connection_url(), additional_headers={"Authorization": key},
                               open_timeout=20, close_timeout=5, max_size=8 * 1024 * 1024) as ws:
                begun, terminated, sent_all = asyncio.Event(), asyncio.Event(), asyncio.Event()
                receiver = asyncio.create_task(receive(ws, audit, turns, begun, terminated, sent_all))
                sender = None
                try:
                    begin_wait = asyncio.create_task(begun.wait())
                    try:
                        done, _ = await asyncio.wait([begin_wait, receiver], timeout=20,
                                                     return_when=asyncio.FIRST_COMPLETED)
                        if receiver in done:
                            await receiver
                        if not begun.is_set():
                            raise RuntimeError("Timed out waiting for AssemblyAI Begin")
                    finally:
                        begin_wait.cancel()
                        await asyncio.gather(begin_wait, return_exceptions=True)
                    sender = asyncio.create_task(send_audio(ws, audio, sent_all))
                    done, _ = await asyncio.wait([sender, receiver], return_when=asyncio.FIRST_COMPLETED)
                    if receiver in done:
                        await receiver
                    await sender
                    await asyncio.wait_for(receiver, timeout=60)
                    if not terminated.is_set():
                        raise RuntimeError("Missing AssemblyAI Termination")
                finally:
                    for task in (sender, receiver):
                        if task is not None:
                            task.cancel()
                    await asyncio.gather(*(t for t in (sender, receiver) if t is not None), return_exceptions=True)
    # Publish the final file only after a complete, successful session.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=output.parent,
                                         prefix=".transcript-", delete=False) as stream:
            temporary = stream.name
            json.dump(turns.result(), stream, ensure_ascii=False)
            stream.write("\n")
        os.replace(temporary, output)
    finally:
        if temporary:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", help="16 kHz mono 16-bit PCM WAV")
    parser.add_argument("output", help="Final transcript JSON path")
    args = parser.parse_args()
    os.umask(0o077)
    try:
        asyncio.run(run(args.audio, args.output))
    except KeyboardInterrupt:
        print("Transcription interrupted", file=sys.stderr)
        return 130
    except Exception as exc:
        # Exception messages from network libraries may contain server data.
        detail = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
        print(f"Transcription failed: {detail}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
