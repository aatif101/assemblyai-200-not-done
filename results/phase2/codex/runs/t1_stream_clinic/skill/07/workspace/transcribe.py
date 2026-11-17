"""Paced PCM -> AssemblyAI v3, with server-side redaction and raw audit frames."""

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
    # Omit policies to redact ALL supported PII entity types.
    "redact_pii_sub": "entity_name",
    "include_partial_turns": "false",
    # Explicit silence settings apply even with the diarization profile enabled.
    "min_turn_silence": 512,
    "max_turn_silence": 2560,
}


class TranscriptionError(Exception):
    pass


def check_begin(message):
    config = message.get("configuration", {})
    required = {"model": MODEL, "domain": "medical-v1",
                "speaker_labels": True, "redact_pii": True}
    if any(config.get(key) != value for key, value in required.items()):
        raise TranscriptionError("AssemblyAI did not confirm the required model, medical mode, diarization, and redaction; no audio sent.")


def read_message(raw, audit):
    # Preserve the received JSON bytes, including key order and whitespace.
    data = raw.encode("utf-8") if isinstance(raw, str) else raw
    audit.write(data + b"\n")
    audit.flush()
    message = json.loads(data)
    if not isinstance(message, dict):
        raise TranscriptionError("Expected a JSON object from AssemblyAI.")
    if message.get("type") == "Error" or "error" in message:
        # Don't echo server payloads: they might contain sensitive content.
        raise TranscriptionError("AssemblyAI reported an error; see the frames audit log.")
    return message


async def send_audio(ws, audio):
    # 800 frames = 50ms at 16 kHz. Pad only the last short chunk.
    # Wait for each chunk's audio duration before sending it; never catch up
    # with a burst after scheduler/network delays.
    while True:
        chunk = audio.readframes(800)
        if not chunk:
            break
        chunk = chunk.ljust(1600, b"\0")
        await asyncio.sleep(0.05)
        await ws.send(chunk)
    await ws.send(json.dumps({"type": "Terminate"}))


async def receive_turns(ws, audit):
    turns = {}
    revisions = {}
    while True:
        message = read_message(await asyncio.wait_for(ws.recv(), 60), audit)
        kind = message.get("type")
        if kind == "Turn":
            if message.get("end_of_turn") is not True:
                raise TranscriptionError("Unexpected partial transcript despite partials being disabled.")
            order = message["turn_order"]
            turns[order] = {"speaker": message.get("speaker_label") or "UNKNOWN",
                            "text": message["transcript"]}
            if order in revisions:
                turns[order]["speaker"] = revisions[order]
        elif kind == "SpeakerRevision":
            for revision in message["revisions"]:
                order = revision["turn_order"]
                label = revision.get("speaker_label") or "UNKNOWN"
                revisions[order] = label
                if order in turns:
                    turns[order]["speaker"] = label
        elif kind == "Termination":
            return {"turns": [turns[order] for order in sorted(turns)]}


def write_result(output, result):
    # Publish only a complete result, after Termination and final revisions.
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                         dir=output.parent, delete=False) as handle:
            temp_path = Path(handle.name)
            json.dump(result, handle, ensure_ascii=False)
            handle.write("\n")
        os.replace(temp_path, output)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


async def transcribe(audio_path, output):
    key = os.environ.get("ASSEMBLYAI_API_KEY")
    if not key:
        raise TranscriptionError("ASSEMBLYAI_API_KEY must be set.")
    frames_path = Path(str(output) + ".frames.jsonl")
    for target in (output, frames_path):
        if target.resolve() == audio_path.resolve() or (target.exists() and os.path.samefile(target, audio_path)):
            raise TranscriptionError("Output paths must differ from the input audio.")
        if target.exists():
            raise TranscriptionError("Output or frames file already exists; choose a new output path.")
    with wave.open(str(audio_path), "rb") as audio:
        if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate(), audio.getcomptype()) != (1, 2, 16000, "NONE"):
            raise TranscriptionError("Input must be uncompressed 16-bit, 16 kHz mono WAV.")
        if audio.getnframes() == 0:
            raise TranscriptionError("Input WAV is empty.")
        # Exclusive creation avoids overwriting an earlier audit log.
        fd = os.open(frames_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as audit:
            async with connect(ENDPOINT + "?" + urlencode(PARAMS),
                               additional_headers={"Authorization": key},
                               open_timeout=20, close_timeout=5, max_size=None) as ws:
                begin = read_message(await asyncio.wait_for(ws.recv(), 20), audit)
                if begin.get("type") != "Begin":
                    raise TranscriptionError("Expected AssemblyAI Begin before sending audio.")
                check_begin(begin)
                print("Streaming audio in real time; collecting redacted final turns.", file=sys.stderr)
                sender = asyncio.create_task(send_audio(ws, audio))
                receiver = asyncio.create_task(receive_turns(ws, audit))
                try:
                    # A receive error stops audio immediately. A sender error
                    # likewise stops waiting for a session that cannot complete.
                    done, _ = await asyncio.wait((sender, receiver), return_when=asyncio.FIRST_COMPLETED)
                    for task in done:
                        task.result()
                    if receiver in done and not sender.done():
                        raise TranscriptionError("AssemblyAI terminated before all audio was sent.")
                    await sender
                    result = await asyncio.wait_for(receiver, 60)
                finally:
                    for task in (sender, receiver):
                        task.cancel()
                    await asyncio.gather(sender, receiver, return_exceptions=True)
        write_result(output, result)
        print(f"Complete: {len(result['turns'])} turns saved.", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        asyncio.run(transcribe(args.audio, args.output))
    except KeyboardInterrupt:
        print("Interrupted; no final output written.", file=sys.stderr)
        return 130
    except TranscriptionError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    except Exception as error:
        # Avoid dumping server payloads, credentials, or transcript text.
        print(f"Transcription failed ({type(error).__name__}); no final output written.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
