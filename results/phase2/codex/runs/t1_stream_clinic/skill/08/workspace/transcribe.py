"""Paced WAV -> AssemblyAI v3, with server-side PII redaction."""

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
    # Omit policies to use ALL supported PII policies.
    "redact_pii_sub": "entity_name",
    "include_partial_turns": "false",
    # Diarization overrides mode presets, so set silence windows explicitly.
    "min_turn_silence": 800,
    "max_turn_silence": 2560,
}


class TranscriptionError(Exception):
    pass


def validate_begin(message):
    config = message.get("configuration", {})
    if not (
        config.get("model") == MODEL
        and config.get("domain") == "medical-v1"
        and config.get("speaker_labels") is True
        and config.get("redact_pii") is True
    ):
        raise TranscriptionError("Server did not confirm required transcription settings")


def apply_message(message, turns, revisions):
    kind = message.get("type")
    if kind == "Turn":
        if message.get("end_of_turn") is not True:
            raise TranscriptionError("Server sent a partial despite partials being disabled")
        order = message["turn_order"]
        turns[order] = {
            "speaker": revisions.get(order, message.get("speaker_label")) or "unknown",
            "text": message["transcript"],
        }
    elif kind == "SpeakerRevision":
        for revision in message["revisions"]:
            order = revision["turn_order"]
            speaker = revision.get("speaker_label") or "unknown"
            revisions[order] = speaker
            if order in turns:
                turns[order]["speaker"] = speaker
    elif kind == "Error" or message.get("error"):
        # Do not echo server text, which might contain sensitive content.
        raise TranscriptionError("AssemblyAI reported a streaming error; see audit log")


async def send_audio(ws, source):
    # A chunk becomes available after its audio duration has elapsed. Pace
    # relative to the last send so stalls cannot cause a catch-up burst.
    loop = asyncio.get_running_loop()
    last_send = loop.time()
    while data := source.readframes(800):  # 50 ms at 16 kHz
        if len(data) < 1600:
            data += b"\0" * (1600 - len(data))
        await asyncio.sleep(max(0, last_send + 0.05 - loop.time()))
        await ws.send(data)
        last_send = loop.time()
    await ws.send('{"type":"Terminate"}')


async def stream(source, audit, api_key):
    turns, revisions = {}, {}
    async with connect(
        ENDPOINT + "?" + urlencode(PARAMS),
        additional_headers={"Authorization": api_key},
        open_timeout=20,
        close_timeout=5,
        max_size=8 * 1024 * 1024,
    ) as ws:
        async def receive(timeout):
            raw = await asyncio.wait_for(ws.recv(), timeout)
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            # Preserve the original JSON message, including all unknown fields.
            audit.write(raw + "\n")
            audit.flush()
            message = json.loads(raw)
            if not isinstance(message, dict):
                raise TranscriptionError("Expected a JSON object from AssemblyAI")
            return message

        first = await receive(30)
        if first.get("type") != "Begin":
            raise TranscriptionError("Expected session Begin from AssemblyAI")
        validate_begin(first)

        async def receive_until_termination():
            while True:
                message = await receive(60)
                apply_message(message, turns, revisions)
                if message.get("type") == "Termination":
                    return

        sender = asyncio.create_task(send_audio(ws, source))
        receiver = asyncio.create_task(receive_until_termination())
        try:
            # Observe either side's failure immediately, including clean socket
            # closures without an Error frame. Success requires BOTH to finish.
            done, _ = await asyncio.wait(
                [sender, receiver], return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                task.result()
            if receiver in done and not sender.done():
                raise TranscriptionError("Session ended before all audio was sent")
            await sender
            await asyncio.wait_for(receiver, 60)
        finally:
            for task in (sender, receiver):
                if not task.done():
                    task.cancel()
            await asyncio.gather(sender, receiver, return_exceptions=True)
    return {"turns": [turns[order] for order in sorted(turns)]}


def private_file(path):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    return os.fdopen(fd, "w", encoding="utf-8", newline="")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    audit_path = Path(str(args.output) + ".frames.jsonl")
    if args.audio.resolve() in (args.output.resolve(), audit_path.resolve()):
        raise TranscriptionError("Input and output paths must differ")
    api_key = os.environ.get("ASSEMBLYAI_API_KEY")
    if not api_key:
        raise TranscriptionError("ASSEMBLYAI_API_KEY is required")
    with wave.open(str(args.audio), "rb") as source:
        if (source.getframerate(), source.getnchannels(), source.getsampwidth(),
                source.getcomptype()) != (16000, 1, 2, "NONE"):
            raise TranscriptionError("Audio must be 16 kHz mono 16-bit PCM WAV")
        if source.getnframes() == 0:
            raise TranscriptionError("Audio is empty")
        # Remove any prior success artifact so failure cannot look successful.
        args.output.unlink(missing_ok=True)
        with private_file(audit_path) as audit:
            result = asyncio.run(stream(source, audit, api_key))
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=args.output.parent,
            prefix=".transcript-", delete=False
        ) as output:
            temp_name = output.name
            json.dump(result, output, ensure_ascii=False)
            output.write("\n")
        os.replace(temp_name, args.output)
    finally:
        if temp_name:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(temp_name)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Transcription interrupted", file=sys.stderr)
        sys.exit(130)
    except Exception as exc:
        # No traceback or arbitrary exception bodies: they can expose server
        # payloads or authentication headers.
        detail = str(exc) if isinstance(exc, TranscriptionError) else type(exc).__name__
        print(f"Transcription failed: {detail}", file=sys.stderr)
        sys.exit(1)
