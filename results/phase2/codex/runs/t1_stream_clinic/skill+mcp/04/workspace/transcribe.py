"""Paced PCM streaming with server-side redaction and a raw message audit log."""

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


class TranscriptionError(Exception):
    pass


def connection_params():
    return {
        "speech_model": MODEL,
        "sample_rate": 16000,
        "encoding": "pcm_s16le",
        "domain": "medical-v1",
        "speaker_labels": "true",
        "speaker_labels_revision_interval_ms": 300000,
        "redact_pii": "true",
        # Omit policies to redact ALL supported entity types.
        "redact_pii_sub": "entity_name",
        "include_partial_turns": "false",
        "min_turn_silence": 800,
        "max_turn_silence": 3600,
        "session_heartbeat": "true",
    }


def validate_begin(message):
    if message.get("type") != "Begin":
        raise TranscriptionError("AssemblyAI did not start a session.")
    config = message.get("configuration", {})
    expected = {"model": MODEL, "domain": "medical-v1",
                "speaker_labels": True, "redact_pii": True}
    if any(config.get(key) != value for key, value in expected.items()):
        raise TranscriptionError("AssemblyAI did not confirm the required model, Medical Mode, speaker labels and PII redaction; no audio sent.")


async def send_audio(ws, audio):
    # 50 ms of mono 16-bit PCM. WAV headers are never sent.
    chunk_frames = 800
    loop = asyncio.get_running_loop()
    deadline = loop.time()
    while True:
        chunk = audio.readframes(chunk_frames)
        if not chunk:
            break
        # Pad only the last short chunk to the API's minimum 50 ms.
        chunk = chunk.ljust(chunk_frames * 2, b"\0")
        # Wait until these samples would have arrived from a phone line.
        # Reset after delays instead of bursting to catch up.
        deadline = max(deadline, loop.time()) + 0.05
        await asyncio.sleep(max(0, deadline - loop.time()))
        await ws.send(chunk)
    await ws.send(json.dumps({"type": "Terminate"}))


async def receive_message(ws, audit):
    raw = await asyncio.wait_for(ws.recv(), timeout=60)
    if not isinstance(raw, str):
        raise TranscriptionError("Unexpected binary message from AssemblyAI.")
    # Preserve the original payload, including all fields and their ordering.
    audit.write(raw + "\n")
    audit.flush()
    message = json.loads(raw)
    if not isinstance(message, dict):
        raise TranscriptionError("AssemblyAI returned a non-object message.")
    if message.get("type") == "Error" or message.get("error"):
        # Never print server payloads: errors could contain sensitive text.
        raise TranscriptionError("AssemblyAI returned an error; see the audit log.")
    return message


async def collect_turns(ws, audit, turns):
    while True:
        message = await receive_message(ws, audit)
        kind = message.get("type")
        if kind == "Termination":
            return
        if kind == "Turn":
            if message.get("end_of_turn") is not True:
                raise TranscriptionError("AssemblyAI unexpectedly returned a partial turn despite partials being disabled.")
            text = message.get("transcript")
            order = message.get("turn_order")
            if not isinstance(text, str) or not isinstance(order, int):
                raise TranscriptionError("Malformed final turn from AssemblyAI.")
            turns[order] = {"speaker": message.get("speaker_label") or "UNKNOWN",
                            "text": text}
        elif kind == "SpeakerRevision":
            for revision in message.get("revisions", []):
                order = revision["turn_order"]
                if order not in turns:
                    raise TranscriptionError("Speaker revision refers to an unknown turn.")
                # Revisions may contain word text. Never use it to replace
                # the already redacted final transcript.
                turns[order]["speaker"] = revision.get("speaker_label") or "UNKNOWN"


def write_result(output, turns):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                         dir=output.parent, delete=False) as handle:
            temporary = handle.name
            json.dump({"turns": [turns[key] for key in sorted(turns)]},
                      handle, ensure_ascii=False)
            handle.write("\n")
        os.replace(temporary, output)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


async def transcribe(source, output, api_key, endpoint=ENDPOINT):
    audit_path = Path(str(output) + ".frames.jsonl")
    if source.resolve() in (output.resolve(), audit_path.resolve()):
        raise TranscriptionError("Input audio and output paths must differ.")
    with wave.open(str(source), "rb") as audio:
        if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate(),
            audio.getcomptype()) != (1, 2, 16000, "NONE"):
            raise TranscriptionError("Audio must be an uncompressed 16 kHz mono 16-bit PCM WAV.")
        if audio.getnframes() == 0:
            raise TranscriptionError("Audio file is empty.")
        # Restrict file permissions even when replacing an existing audit file.
        fd = os.open(audit_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as audit:
            url = endpoint + "?" + urlencode(connection_params())
            async with connect(url, additional_headers={"Authorization": api_key},
                               open_timeout=20, close_timeout=5, max_size=8 * 1024 * 1024) as ws:
                validate_begin(await receive_message(ws, audit))
                turns = {}
                sender = asyncio.create_task(send_audio(ws, audio))
                receiver = asyncio.create_task(collect_turns(ws, audit, turns))
                try:
                    done, _ = await asyncio.wait({sender, receiver}, return_when=asyncio.FIRST_COMPLETED)
                    if receiver in done and not sender.done():
                        await receiver  # Propagate errors before checking early close.
                        raise TranscriptionError("AssemblyAI terminated before all audio was sent.")
                    await sender
                    await asyncio.wait_for(receiver, timeout=60)
                finally:
                    for task in (sender, receiver):
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(sender, receiver, return_exceptions=True)
    write_result(output, turns)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
    if not key:
        print("ASSEMBLYAI_API_KEY is required.", file=sys.stderr)
        return 1
    try:
        asyncio.run(transcribe(args.audio, args.output, key))
    except TranscriptionError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Transcription interrupted.", file=sys.stderr)
        return 130
    except Exception as exc:
        # Exception strings can include response bodies or URLs. Keep them out
        # of terminal/automation logs.
        print(f"Transcription failed ({type(exc).__name__}); no new final transcript written.", file=sys.stderr)
        return 1
    print("Transcription complete.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
