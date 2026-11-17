#!/usr/bin/env python3
"""Paced WAV -> AssemblyAI v3, preserving raw server JSON for audit."""
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
    "redact_pii_sub": "entity_name",
    "include_partial_turns": "false",
    # Omit redact_pii_policies to enable every supported policy.
}


class Transcript:
    def __init__(self):
        self.turns = {}
        self.labels = {}

    def accept(self, message):
        kind = message.get("type")
        if kind == "Error":
            # Do not echo server payloads (which may contain sensitive data).
            raise RuntimeError("AssemblyAI reported an error; see the frames audit log")
        if kind == "Turn":
            if message.get("end_of_turn") is not True:
                raise RuntimeError("Unexpected partial turn despite partials being disabled")
            order = message["turn_order"]
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

    def result(self):
        return {"turns": [self.turns[key] for key in sorted(self.turns)]}


async def stream_audio(ws, audio, state, terminated):
    """Send 50ms chunks, including a zero-padded final short chunk."""
    loop = asyncio.get_running_loop()
    next_send = loop.time()
    while True:
        chunk = audio.readframes(800)
        if not chunk:
            break
        if len(chunk) < 1600:
            chunk += b"\0" * (1600 - len(chunk))
        await asyncio.sleep(max(0, next_send - loop.time()))
        await ws.send(chunk)
        # Never catch up with a burst after a slow network send.
        next_send = loop.time() + 0.05
    await asyncio.sleep(max(0, next_send - loop.time()))
    state["terminate_sent"] = True
    await ws.send('{"type":"Terminate"}')
    await asyncio.wait_for(terminated.wait(), timeout=60)


async def transcribe(audio, audit, key):
    transcript = Transcript()
    terminated = asyncio.Event()
    state = {"terminate_sent": False}
    url = "wss://streaming.assemblyai.com/v3/ws?" + urlencode(PARAMS)
    async with connect(
        url, additional_headers={"Authorization": key},
        open_timeout=20, close_timeout=5, max_size=16 * 1024 * 1024,
    ) as ws:
        async def receive():
            raw = await ws.recv()
            if not isinstance(raw, str):
                raise RuntimeError("Expected a JSON text message from AssemblyAI")
            # Preserve the exact JSON payload, without SDK parsing/reserialization.
            audit.write(raw.encode("utf-8") + b"\n")
            audit.flush()
            message = json.loads(raw)
            transcript.accept(message)
            return message

        begin = await asyncio.wait_for(receive(), timeout=20)
        config = begin.get("configuration", {})
        expected = {"model": MODEL, "domain": "medical-v1",
                    "speaker_labels": True, "redact_pii": True}
        if begin.get("type") != "Begin" or any(
            config.get(k) != v for k, v in expected.items()
        ):
            await ws.send('{"type":"Terminate"}')
            # Drain all messages even when the applied configuration is wrong.
            async def drain():
                while (await receive()).get("type") != "Termination":
                    pass
            await asyncio.wait_for(drain(), timeout=20)
            raise RuntimeError("AssemblyAI did not confirm the required model/medical/privacy/speaker settings")

        async def reader():
            while True:
                message = await receive()
                if message.get("type") == "Termination":
                    if not state["terminate_sent"]:
                        raise RuntimeError("AssemblyAI terminated before all audio was sent")
                    terminated.set()
                    return

        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(reader())
            tasks.create_task(stream_audio(ws, audio, state, terminated))
    return transcript.result()


def main():
    if len(sys.argv) != 3:
        raise ValueError("Usage: ./run.sh <audio.wav> <out.json>")
    key = os.environ.get("ASSEMBLYAI_API_KEY")
    if not key:
        raise ValueError("ASSEMBLYAI_API_KEY is required")
    source, output = map(Path, sys.argv[1:])
    frames = Path(str(output) + ".frames.jsonl")
    if source.resolve() in (output.resolve(), frames.resolve()):
        raise ValueError("Input and output paths must be different")
    with wave.open(str(source), "rb") as audio:
        if (audio.getframerate(), audio.getnchannels(), audio.getsampwidth(),
                audio.getcomptype()) != (16000, 1, 2, "NONE"):
            raise ValueError("Audio must be uncompressed 16 kHz mono 16-bit PCM WAV")
        if audio.getnframes() == 0:
            raise ValueError("Audio is empty")
        # Restrictive permissions for new output files, including temporary files.
        os.umask(0o077)
        with frames.open("wb") as audit:
            result = asyncio.run(transcribe(audio, audit, key))
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                             dir=output.parent, delete=False) as handle:
                temporary = handle.name
                json.dump(result, handle, ensure_ascii=False)
                handle.write("\n")
            os.replace(temporary, output)
        finally:
            if temporary:
                with contextlib.suppress(FileNotFoundError):
                    os.unlink(temporary)
    print(f"Completed: {len(result['turns'])} turns", file=sys.stderr)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Transcription interrupted", file=sys.stderr)
        sys.exit(130)
    except Exception as exc:
        # Avoid logging arbitrary exception text with credentials or transcript data.
        if isinstance(exc, (ValueError, RuntimeError)):
            print(str(exc), file=sys.stderr)
        else:
            print(f"Transcription failed ({type(exc).__name__}); check input, connectivity, account access, and audit log", file=sys.stderr)
        sys.exit(1)
