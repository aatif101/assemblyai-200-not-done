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


class TranscriptionError(Exception):
    pass


def private_open(path):
    return open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600),
                "w", encoding="utf-8", newline="\n")


def save_result(path, turns):
    """Publish only a complete, successfully terminated transcript."""
    fd, temporary = tempfile.mkstemp(prefix=".transcript-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump({"turns": [turns[key] for key in sorted(turns)]},
                      output, ensure_ascii=False)
            output.write("\n")
        os.replace(temporary, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)


async def transcribe(audio_path, output_path, api_key, endpoint=ENDPOINT):
    output_path = Path(output_path)
    frames_path = Path(str(output_path) + ".frames.jsonl")
    if Path(audio_path).resolve() in (output_path.resolve(), frames_path.resolve()):
        raise TranscriptionError("Input and output paths must be different.")

    with wave.open(str(audio_path), "rb") as audio:
        if (audio.getnchannels() != 1 or audio.getsampwidth() != 2
                or audio.getcomptype() != "NONE"
                or not 8000 <= audio.getframerate() <= 96000):
            raise TranscriptionError("Expected mono 16-bit PCM WAV at 8–96 kHz.")
        if audio.getnframes() == 0:
            raise TranscriptionError("Audio file is empty.")
        rate = audio.getframerate()
        params = {
            "sample_rate": rate,
            "encoding": "pcm_s16le",
            "speech_model": MODEL,
            "domain": "medical-v1",
            "speaker_labels": "true",
            "redact_pii": "true",
            # Omitting policies enables ALL policies, including names, phone, SSN.
            "redact_pii_sub": "entity_name",
            "include_partial_turns": "false",
        }
        turns = {}
        begun = asyncio.Event()
        terminating = False

        with private_open(frames_path) as audit:
            async with connect(
                endpoint + "?" + urlencode(params),
                additional_headers={"Authorization": api_key},
                open_timeout=20, close_timeout=5,
            ) as ws:

                async def receive():
                    async for raw in ws:
                        if not isinstance(raw, str):
                            raise TranscriptionError("Unexpected binary server message.")
                        # Preserve the original message, never serialize a parsed copy.
                        audit.write(raw + "\n")
                        audit.flush()
                        message = json.loads(raw)
                        kind = message.get("type")
                        if kind == "Begin":
                            cfg = message.get("configuration", {})
                            if (cfg.get("model") != MODEL
                                    or cfg.get("domain") != "medical-v1"
                                    or cfg.get("speaker_labels") is not True
                                    or cfg.get("redact_pii") is not True):
                                raise TranscriptionError(
                                    "Server did not confirm required model, Medical Mode, "
                                    "speaker labels and PII redaction.")
                            begun.set()
                        elif kind == "Turn":
                            if not message.get("end_of_turn"):
                                raise TranscriptionError(
                                    "Server returned a partial despite partials being disabled.")
                            turns[message["turn_order"]] = {
                                "speaker": message.get("speaker_label") or "UNKNOWN",
                                "text": message["transcript"],
                            }
                        elif kind == "SpeakerRevision":
                            for revision in message["revisions"]:
                                order = revision["turn_order"]
                                if order not in turns:
                                    raise TranscriptionError("Revision references an unknown turn.")
                                turns[order]["speaker"] = revision.get("speaker_label") or "UNKNOWN"
                        elif kind == "Termination":
                            if not terminating:
                                raise TranscriptionError("Server terminated before all audio was sent.")
                            return
                        elif kind == "Error" or message.get("error"):
                            # Never echo server payloads: they could contain patient details.
                            raise TranscriptionError("AssemblyAI reported an error; see audit frames.")
                    raise TranscriptionError("Connection closed without Termination.")

                async def send():
                    nonlocal terminating
                    await asyncio.wait_for(begun.wait(), timeout=20)
                    loop = asyncio.get_running_loop()
                    deadline = loop.time()
                    while chunk := audio.readframes(rate // 20):
                        # Wait for each chunk's duration before sending it. Never catch
                        # up with a burst if the network or scheduler was delayed.
                        deadline = max(deadline, loop.time()) + len(chunk) / (2 * rate)
                        await asyncio.sleep(max(0, deadline - loop.time()))
                        await ws.send(chunk)
                    terminating = True
                    await ws.send(json.dumps({"type": "Terminate"}))

                receiver = asyncio.create_task(receive())
                sender = asyncio.create_task(send())
                try:
                    done, _ = await asyncio.wait(
                        [receiver, sender], return_when=asyncio.FIRST_COMPLETED)
                    for task in done:
                        task.result()
                    if not sender.done():
                        raise TranscriptionError("Server finished before audio transmission completed.")
                    # Includes buffered finals and final diarization corrections.
                    await asyncio.wait_for(receiver, timeout=30)
                finally:
                    for task in (sender, receiver):
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(sender, receiver, return_exceptions=True)
        save_result(output_path, turns)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", help="Mono 16-bit PCM WAV")
    parser.add_argument("output", help="Final transcript JSON path")
    args = parser.parse_args()
    key = os.environ.get("ASSEMBLYAI_API_KEY")
    if not key:
        print("ASSEMBLYAI_API_KEY is required.", file=sys.stderr)
        return 1
    try:
        asyncio.run(transcribe(args.audio, args.output, key))
    except KeyboardInterrupt:
        print("Transcription interrupted.", file=sys.stderr)
        return 130
    except TranscriptionError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception as exc:
        # Exception messages from networking libraries may include request headers.
        print(f"Transcription failed ({type(exc).__name__}).", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
