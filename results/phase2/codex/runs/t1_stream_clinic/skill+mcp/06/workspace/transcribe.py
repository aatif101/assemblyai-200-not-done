"""Real-time WAV replay with server-side PII redaction and an exact frame audit."""

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
PARAMETERS = {
    "speech_model": MODEL,
    "sample_rate": 16000,
    "encoding": "pcm_s16le",
    "domain": "medical-v1",
    "speaker_labels": "true",
    "speaker_labels_revision_interval_ms": 300000,
    "redact_pii": "true",
    # Omit policies to redact all supported PII categories.
    "redact_pii_sub": "entity_name",
    "include_partial_turns": "false",
    # Explicit silence settings also apply with the diarization profile.
    "min_turn_silence": 512,
    "max_turn_silence": 2560,
}


class TranscriptionError(Exception):
    pass


class Turns:
    def __init__(self):
        self.turns = {}

    def accept(self, message):
        kind = message.get("type")
        if kind == "Turn":
            if message.get("end_of_turn") is not True:
                raise TranscriptionError("Unexpected partial transcript despite partials being disabled")
            self.turns[message["turn_order"]] = {
                "speaker": message.get("speaker_label") or "UNKNOWN",
                # Keep the redacted final transcript, never reconstruct from words.
                "text": message["transcript"],
            }
        elif kind == "SpeakerRevision":
            for revision in message["revisions"]:
                order = revision["turn_order"]
                if order not in self.turns:
                    raise TranscriptionError("Speaker revision references a missing turn")
                self.turns[order]["speaker"] = revision.get("speaker_label") or "UNKNOWN"

    def result(self):
        return {"turns": [self.turns[key] for key in sorted(self.turns)]}


async def receive_frame(ws, audit):
    raw = await asyncio.wait_for(ws.recv(), timeout=60)
    if not isinstance(raw, str):
        raise TranscriptionError("Expected a JSON text message from AssemblyAI")
    # Write the original payload, without parsing/re-serializing or adding fields.
    audit.write(raw + "\n")
    audit.flush()
    message = json.loads(raw)
    if not isinstance(message, dict):
        raise TranscriptionError("Expected a JSON object from AssemblyAI")
    if message.get("type") == "Error" or message.get("error"):
        # Do not echo provider payloads or transcripts into terminal logs.
        raise TranscriptionError("AssemblyAI reported an error; see the frame audit")
    return message


def validate_begin(message):
    if message.get("type") != "Begin":
        raise TranscriptionError("AssemblyAI did not begin the session")
    config = message.get("configuration", {})
    required = {
        "model": MODEL,
        "domain": "medical-v1",
        "speaker_labels": True,
        "redact_pii": True,
    }
    for key, expected in required.items():
        if config.get(key) != expected:
            raise TranscriptionError(f"AssemblyAI did not confirm required configuration: {key}")
    if config.get("include_partial_turns") is True:
        raise TranscriptionError("AssemblyAI enabled unredacted partials")


async def send_audio(ws, audio):
    # WAV header is consumed by wave; only little-endian PCM reaches the service.
    # Sleep after every 50ms packet: network stalls never cause catch-up bursts.
    frames_sent = 0
    while chunk := audio.readframes(800):
        frames_sent += len(chunk) // 2
        if len(chunk) < 1600:
            chunk += b"\0" * (1600 - len(chunk))
        await asyncio.wait_for(ws.send(chunk), timeout=30)
        await asyncio.sleep(0.05)
    if frames_sent != audio.getnframes():
        raise TranscriptionError("WAV file is truncated")
    await asyncio.wait_for(ws.send(json.dumps({"type": "Terminate"})), timeout=30)


async def collect(ws, audit, turns, sender):
    while True:
        message = await receive_frame(ws, audit)
        turns.accept(message)
        if message.get("type") == "Termination":
            if not sender.done():
                raise TranscriptionError("Session ended before all audio was sent")
            return


async def transcribe(audio, audit, api_key):
    turns = Turns()
    url = ENDPOINT + "?" + urlencode(PARAMETERS)
    async with connect(
        url,
        additional_headers={"Authorization": api_key},
        open_timeout=30,
        close_timeout=10,
        max_size=16 * 1024 * 1024,
    ) as ws:
        validate_begin(await receive_frame(ws, audit))
        sender = asyncio.create_task(send_audio(ws, audio))
        receiver = asyncio.create_task(collect(ws, audit, turns, sender))
        try:
            await asyncio.gather(sender, receiver)
        finally:
            for task in (sender, receiver):
                if not task.done():
                    task.cancel()
            await asyncio.gather(sender, receiver, return_exceptions=True)
    return turns.result()


def write_result(path, result):
    # Only publish a complete successful result; use restrictive permissions.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".transcript-", delete=False) as output:
            temporary = Path(output.name)
            json.dump(result, output, ensure_ascii=False)
            output.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    api_key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
    if not api_key:
        raise TranscriptionError("Set ASSEMBLYAI_API_KEY in the environment")
    audit_path = Path(str(args.output) + ".frames.jsonl")
    if args.audio.resolve() in (args.output.resolve(), audit_path.resolve()):
        raise TranscriptionError("Output paths must differ from the input audio")
    with wave.open(str(args.audio), "rb") as audio:
        if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate(),
                audio.getcomptype()) != (1, 2, 16000, "NONE"):
            raise TranscriptionError("Audio must be uncompressed 16-bit, 16 kHz mono WAV")
        if audio.getnframes() == 0:
            raise TranscriptionError("Audio is empty")
        fd = os.open(audit_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as audit:
            result = asyncio.run(transcribe(audio, audit, api_key))
        write_result(args.output, result)
    print(f"Transcription complete: {len(result['turns'])} turns", file=sys.stderr)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Transcription interrupted", file=sys.stderr)
        sys.exit(130)
    except Exception as exc:
        # Exception messages from networking libraries may contain response data.
        detail = str(exc) if isinstance(exc, TranscriptionError) else type(exc).__name__
        print(f"Transcription failed: {detail}", file=sys.stderr)
        sys.exit(1)
