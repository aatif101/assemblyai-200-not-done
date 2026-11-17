"""Paced WAV -> AssemblyAI v3; preserve raw events and collect redacted turns."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import wave
from urllib.parse import urlencode

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
    # Omit policies to redact ALL supported entity types.
    "redact_pii_sub": "entity_name",
    "include_partial_turns": "false",
    # Explicit overrides: diarization ignores the model's mode presets.
    "min_turn_silence": 512,
    "max_turn_silence": 2560,
}


class TranscriptionError(Exception):
    pass


def validate_begin(event):
    config = event.get("configuration", {})
    expected = {"model": MODEL, "domain": "medical-v1",
                "speaker_labels": True, "redact_pii": True}
    if event.get("type") != "Begin" or any(
        config.get(key) != value for key, value in expected.items()
    ):
        raise TranscriptionError("Server did not confirm the required transcription features; no audio sent.")


def speaker_label(value):
    return value if isinstance(value, str) and len(value) == 1 and "A" <= value <= "Z" else "UNKNOWN"


async def stream(wav, audit, api_key, endpoint=ENDPOINT):
    turns = {}
    revisions = {}
    terminate_sent = False
    url = endpoint + "?" + urlencode(PARAMS)
    async with connect(url, additional_headers={"Authorization": api_key},
                       open_timeout=20, close_timeout=5, max_size=8 * 1024 * 1024) as ws:
        async def receive():
            raw = await asyncio.wait_for(ws.recv(), timeout=60)
            if not isinstance(raw, str):
                raise TranscriptionError("Unexpected binary response from AssemblyAI.")
            # Preserve the original JSON payload, including every field and its formatting.
            audit.write(raw + "\n")
            audit.flush()
            event = json.loads(raw)
            if not isinstance(event, dict):
                raise TranscriptionError("Expected a JSON object from AssemblyAI.")
            if event.get("type") == "Error" or "error" in event:
                # Do not echo remote payloads, which could contain sensitive information.
                raise TranscriptionError("AssemblyAI reported an error; see the audit log.")
            return event

        validate_begin(await receive())

        async def send_audio():
            nonlocal terminate_sent
            samples_read = 0
            while True:
                chunk = wav.readframes(800)  # 50 ms, 16-bit mono PCM; never send the WAV header.
                if not chunk:
                    break
                samples_read += len(chunk) // 2
                if len(chunk) % 2:
                    raise TranscriptionError("Truncated PCM sample in WAV file.")
                chunk = chunk.ljust(1600, b"\0")
                started = asyncio.get_running_loop().time()
                await ws.send(chunk)
                # Relative to the actual send: stalls never cause a catch-up burst.
                await asyncio.sleep(max(0, started + 0.05 - asyncio.get_running_loop().time()))
            if samples_read != wav.getnframes():
                raise TranscriptionError("WAV data is truncated.")
            terminate_sent = True
            await ws.send('{"type":"Terminate"}')

        async def receive_events():
            while True:
                event = await receive()
                kind = event.get("type")
                if kind == "Turn":
                    if event.get("end_of_turn") is not True:
                        raise TranscriptionError("Unexpected partial turn despite disabled partials.")
                    order = event["turn_order"]
                    text = event.get("utterance") or event["transcript"]
                    if not isinstance(order, int) or not isinstance(text, str):
                        raise TranscriptionError("Invalid final turn from AssemblyAI.")
                    turns[order] = {"speaker": speaker_label(event.get("speaker_label")), "text": text}
                elif kind == "SpeakerRevision":
                    for revision in event["revisions"]:
                        revisions[revision["turn_order"]] = speaker_label(revision.get("speaker_label"))
                elif kind == "Termination":
                    if not terminate_sent:
                        raise TranscriptionError("Session ended before all audio was sent.")
                    return

        tasks = [asyncio.create_task(send_audio()), asyncio.create_task(receive_events())]
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    for order, label in revisions.items():
        if order in turns:
            turns[order]["speaker"] = label
    return {"turns": [turns[order] for order in sorted(turns)]}


def write_result(path, result):
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".transcript-", delete=False) as file:
            temp_path = Path(file.name)
            json.dump(result, file, ensure_ascii=False)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        api_key = os.environ.get("ASSEMBLYAI_API_KEY")
        if not api_key:
            raise TranscriptionError("ASSEMBLYAI_API_KEY is not set.")
        audit_path = Path(str(args.output) + ".frames.jsonl")
        paths = [args.audio.resolve(), args.output.resolve(), audit_path.resolve()]
        if len(set(paths)) != 3 or any(
            a.exists() and b.exists() and os.path.samefile(a, b)
            for a, b in [(args.audio, args.output), (args.audio, audit_path), (args.output, audit_path)]
        ):
            raise TranscriptionError("Input, output, and audit paths must be distinct.")
        with wave.open(str(args.audio), "rb") as wav:
            if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getcomptype()) != (1, 2, 16000, "NONE"):
                raise TranscriptionError("Input must be a 16 kHz mono 16-bit PCM WAV.")
            if wav.getnframes() == 0:
                raise TranscriptionError("Input WAV contains no audio.")
            # Remove stale results so a failed rerun cannot be mistaken for success.
            args.output.unlink(missing_ok=True)
            with audit_path.open("w", encoding="utf-8", newline="\n") as audit:
                result = asyncio.run(stream(wav, audit, api_key))
            write_result(args.output, result)
        print(f"Transcribed {len(result['turns'])} turns.", file=sys.stderr)
        return 0
    except KeyboardInterrupt:
        print("Transcription interrupted.", file=sys.stderr)
        return 130
    except Exception as exc:
        # Avoid logging arbitrary exceptions, URLs, remote transcripts, or credentials.
        message = str(exc) if isinstance(exc, TranscriptionError) else type(exc).__name__
        print(f"Transcription failed: {message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
