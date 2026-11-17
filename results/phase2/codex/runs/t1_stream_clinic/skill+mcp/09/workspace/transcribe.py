"""Paced WAV -> AssemblyAI v3, with an exact server-message audit trail."""

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


class TranscriptionError(Exception):
    pass


class Transcript:
    def __init__(self):
        self.turns = {}

    def accept(self, message):
        kind = message.get("type")
        if kind == "Turn":
            if message.get("end_of_turn") is not True:
                raise TranscriptionError("Unexpected unredacted partial turn from server.")
            # Repeated/final formatted versions replace earlier versions of this turn.
            self.turns[message["turn_order"]] = {
                "speaker": message.get("speaker_label") or "PENDING",
                "text": message["transcript"],
            }
        elif kind == "SpeakerRevision":
            for revision in message["revisions"]:
                order = revision["turn_order"]
                if order not in self.turns:
                    raise TranscriptionError("Speaker correction references an unknown turn.")
                # Never rebuild redacted text from revision words.
                self.turns[order]["speaker"] = revision.get("speaker_label") or "PENDING"
        elif kind == "Error" or message.get("error"):
            # Do not echo service payloads: they may contain personal information.
            raise TranscriptionError("AssemblyAI reported an error; see the audit log.")

    def result(self):
        return {"turns": [self.turns[k] for k in sorted(self.turns)]}


def validate_begin(message):
    if message.get("type") != "Begin":
        raise TranscriptionError("Expected AssemblyAI Begin message.")
    config = message.get("configuration", {})
    for key, expected in {
        "model": MODEL,
        "domain": "medical-v1",
        "speaker_labels": True,
        "redact_pii": True,
    }.items():
        if config.get(key) != expected:
            raise TranscriptionError(f"AssemblyAI did not confirm required setting: {key}.")
    if config.get("include_partial_turns") is True:
        raise TranscriptionError("AssemblyAI enabled unsafe partial turns.")


async def receive_message(ws, audit):
    raw = await ws.recv()
    # Preserve the original JSON payload, including all fields and whitespace.
    payload = raw.encode("utf-8") if isinstance(raw, str) else raw
    audit.write(payload + b"\n")
    audit.flush()
    message = json.loads(payload)
    if not isinstance(message, dict):
        raise TranscriptionError("Expected a JSON object from AssemblyAI.")
    return message


async def send_audio(ws, audio):
    rate = audio.getframerate()
    frames_per_chunk = rate // 20  # 50 ms, the minimum permitted frame duration.
    bytes_per_chunk = frames_per_chunk * 2
    loop = asyncio.get_running_loop()
    deadline = loop.time()
    while True:
        chunk = audio.readframes(frames_per_chunk)
        if not chunk:
            break
        # The last chunk must also be >=50ms. Pad only the final short chunk.
        if len(chunk) < bytes_per_chunk:
            chunk += b"\0" * (bytes_per_chunk - len(chunk))
        duration = len(chunk) / (2 * rate)
        # Wait for these samples to have "arrived" before sending them. If the
        # network stalls, reset the clock rather than bursting to catch up.
        deadline = max(deadline, loop.time()) + duration
        await asyncio.sleep(max(0, deadline - loop.time()))
        await ws.send(chunk)
    await ws.send('{"type":"Terminate"}')


async def receive_transcript(ws, audit, transcript, terminating):
    while True:
        message = await asyncio.wait_for(receive_message(ws, audit), timeout=60)
        if message.get("type") == "Termination":
            if not terminating.done() or terminating.cancelled() or terminating.exception():
                raise TranscriptionError("Server terminated before all audio was sent.")
            return
        transcript.accept(message)


async def transcribe(audio, audit, key):
    params = {
        "sample_rate": audio.getframerate(),
        "encoding": "pcm_s16le",
        "speech_model": MODEL,
        "domain": "medical-v1",
        "speaker_labels": "true",
        "speaker_labels_revision_interval_ms": 300000,
        "redact_pii": "true",
        "redact_pii_sub": "entity_name",
        "include_partial_turns": "false",
        # No policies override: redact ALL supported PII categories.
    }
    transcript = Transcript()
    async with connect(
        ENDPOINT + "?" + urlencode(params),
        additional_headers={"Authorization": key},
        open_timeout=20,
        close_timeout=5,
        max_size=8 * 1024 * 1024,
    ) as ws:
        begin = await asyncio.wait_for(receive_message(ws, audit), timeout=20)
        validate_begin(begin)  # Fail before sending audio if protections are absent.
        print("Connected: medical transcription, speaker labels, PII masking enabled.", file=sys.stderr)
        sender = asyncio.create_task(send_audio(ws, audio))
        receiver = asyncio.create_task(receive_transcript(ws, audit, transcript, sender))
        try:
            await asyncio.gather(sender, receiver)
        finally:
            for task in (sender, receiver):
                if not task.done():
                    task.cancel()
            await asyncio.gather(sender, receiver, return_exceptions=True)
    return transcript.result()


def write_result(path, result):
    # Only publish a final transcript after graceful termination and corrections.
    fd, temporary = tempfile.mkstemp(prefix=".transcript-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(result, stream, ensure_ascii=False)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
    if not key:
        raise TranscriptionError("Set ASSEMBLYAI_API_KEY in the environment.")
    audit_path = Path(str(args.output) + ".frames.jsonl")
    if args.audio.resolve() in (args.output.resolve(), audit_path.resolve()):
        raise TranscriptionError("Input audio and output paths must be different.")
    with wave.open(str(args.audio), "rb") as audio:
        if (audio.getnchannels(), audio.getsampwidth(), audio.getcomptype()) != (1, 2, "NONE"):
            raise TranscriptionError("Input must be an uncompressed 16-bit mono PCM WAV.")
        if not 8000 <= audio.getframerate() <= 96000 or audio.getnframes() == 0:
            raise TranscriptionError("Input must contain audio at 8–96 kHz.")
        # Protect newly created files from other local users.
        os.umask(0o077)
        with audit_path.open("wb") as audit:
            result = asyncio.run(transcribe(audio, audit, key))
        write_result(args.output, result)
    print(f"Completed: {len(result['turns'])} final turns.", file=sys.stderr)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Transcription interrupted.", file=sys.stderr)
        sys.exit(130)
    except Exception as exc:
        # Exceptions from networking libraries can include request details.
        detail = str(exc) if isinstance(exc, TranscriptionError) else type(exc).__name__
        print(f"Transcription failed: {detail}", file=sys.stderr)
        sys.exit(1)
