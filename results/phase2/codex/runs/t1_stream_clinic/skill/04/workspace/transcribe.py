#!/usr/bin/env python3
"""Replay PCM WAV audio through AssemblyAI's streaming v3 API."""
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


class SessionError(Exception):
    pass


def private_file(path):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    return os.fdopen(fd, "wb", buffering=0)


async def read_message(socket, audit):
    raw = await asyncio.wait_for(socket.recv(), timeout=60)
    # Preserve the actual payload: no SDK conversion or JSON reserialization.
    payload = raw.encode("utf-8") if isinstance(raw, str) else raw
    audit.write(payload + b"\n")
    message = json.loads(payload)
    if not isinstance(message, dict):
        raise SessionError("Server sent a non-object message")
    if message.get("type") == "Error" or message.get("error"):
        # Server error text can contain sensitive data. It belongs only in the audit.
        raise SessionError("AssemblyAI reported an error; see the audit log")
    return message


def check_begin(message):
    if message.get("type") != "Begin":
        raise SessionError("Expected a Begin message")
    config = message.get("configuration", {})
    expected = {"model": MODEL, "domain": "medical-v1",
                "speaker_labels": True, "redact_pii": True}
    for field, value in expected.items():
        if config.get(field) != value:
            raise SessionError(f"Server did not confirm required setting: {field}")


async def send_audio(socket, audio):
    chunk_frames = audio.getframerate() // 20  # 50 ms, without the WAV header
    chunk_bytes = chunk_frames * 2
    while chunk := audio.readframes(chunk_frames):
        # Pad the last chunk to the API's minimum of 50 ms.
        chunk = chunk.ljust(chunk_bytes, b"\x00")
        # Wait for each chunk's duration before sending it. Never catch up in bursts.
        await asyncio.sleep(chunk_frames / audio.getframerate())
        await socket.send(chunk)
    await socket.send(json.dumps({"type": "Terminate"}))


async def receive_turns(socket, audit, turns):
    while True:
        message = await read_message(socket, audit)
        kind = message.get("type")
        if kind == "Turn":
            if message.get("end_of_turn") is not True:
                raise SessionError("Received a partial turn despite disabling partials")
            order = message["turn_order"]
            text = message["transcript"]
            if not isinstance(order, int) or not isinstance(text, str):
                raise SessionError("Malformed final turn")
            turns[order] = {"speaker": message.get("speaker_label") or "UNKNOWN",
                            "text": text}
        elif kind == "SpeakerRevision":
            for revision in message["revisions"]:
                order = revision["turn_order"]
                if order not in turns:
                    raise SessionError("Speaker revision references a missing turn")
                turns[order]["speaker"] = revision.get("speaker_label") or "UNKNOWN"
        elif kind == "Termination":
            return


def write_result(path, turns):
    # Publish only a complete session; leave no half-written transcript on failure.
    fd, temporary = tempfile.mkstemp(prefix=".transcript-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump({"turns": [turns[n] for n in sorted(turns)]}, output,
                      ensure_ascii=False)
            output.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


async def transcribe(audio_path, output_path):
    key = os.environ.get("ASSEMBLYAI_API_KEY")
    if not key:
        raise SessionError("ASSEMBLYAI_API_KEY is required")
    audit_path = Path(str(output_path) + ".frames.jsonl")
    if audio_path.resolve() in (output_path.resolve(), audit_path.resolve()):
        raise SessionError("Output paths must differ from the input audio")
    with wave.open(str(audio_path), "rb") as audio:
        if (audio.getnchannels() != 1 or audio.getsampwidth() != 2
                or audio.getcomptype() != "NONE" or audio.getframerate() != 16000):
            raise SessionError("Input must be a 16 kHz mono, 16-bit PCM WAV")
        if audio.getnframes() == 0:
            raise SessionError("Input audio is empty")
        params = {
            "speech_model": MODEL, "sample_rate": 16000, "encoding": "pcm_s16le",
            "domain": "medical-v1", "speaker_labels": "true",
            "speaker_labels_revision_interval_ms": 300000,
            "redact_pii": "true", "redact_pii_sub": "entity_name",
            # Omit redact_pii_policies to enable ALL supported policies.
            "include_partial_turns": "false", "session_heartbeat": "true",
            # Explicit settings apply even with the diarization turn profile.
            "min_turn_silence": 512, "max_turn_silence": 2560,
        }
        turns = {}
        with private_file(audit_path) as audit:
            async with connect(ENDPOINT + "?" + urlencode(params),
                               additional_headers={"Authorization": key},
                               open_timeout=30, close_timeout=10,
                               max_size=4 * 1024 * 1024) as socket:
                check_begin(await read_message(socket, audit))
                sender = asyncio.create_task(send_audio(socket, audio))
                receiver = asyncio.create_task(receive_turns(socket, audit, turns))
                try:
                    done, _ = await asyncio.wait(
                        (sender, receiver), return_when=asyncio.FIRST_COMPLETED)
                    for task in done:
                        task.result()
                    if receiver in done and not sender.done():
                        raise SessionError("Session terminated before all audio was sent")
                    await sender
                    await receiver  # includes final speaker revisions before Termination
                finally:
                    for task in (sender, receiver):
                        task.cancel()
                    await asyncio.gather(sender, receiver, return_exceptions=True)
        write_result(output_path, turns)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    try:
        asyncio.run(transcribe(args.audio, args.output))
    except KeyboardInterrupt:
        print("Transcription interrupted", file=sys.stderr)
        return 130
    except Exception as error:
        # Avoid logging server text, transcript content, credentials, or URLs.
        detail = str(error) if isinstance(error, SessionError) else type(error).__name__
        print(f"Transcription failed: {detail}", file=sys.stderr)
        return 1
    print(f"Transcript written to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
