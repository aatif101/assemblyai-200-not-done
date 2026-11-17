#!/usr/bin/env python3
"""Stream PCM WAV audio to AssemblyAI, preserving its redacted wire messages."""
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


# Keep clinical terms visible while masking identifying and contact information.
PII_POLICIES = [
    "person_name", "phone_number", "us_social_security_number", "email_address",
    "location", "date_of_birth", "date", "person_age", "account_number",
    "banking_information", "credit_card_number", "credit_card_expiration",
    "credit_card_cvv", "drivers_license", "healthcare_number", "passport_number",
    "ip_address", "url", "username", "password", "vehicle_id", "number_sequence",
]


class TranscriptionError(Exception):
    pass


def parameters():
    return {
        "sample_rate": 16000,
        "encoding": "pcm_s16le",
        "speech_model": "u3-rt-pro",
        "mode": "max_accuracy",
        "domain": "medical-v1",
        "speaker_labels": "true",
        "format_turns": "true",
        "redact_pii": "true",
        "redact_pii_policies": json.dumps(PII_POLICIES),
        "redact_pii_sub": "entity_name",
        # Do not request speculative partial text before redaction is complete.
        "include_partial_turns": "false",
        "continuous_partials": "false",
        "customer_support_audio_capture": "false",
    }


class Turns:
    def __init__(self):
        self.items = {}
        self.revisions = {}

    def accept(self, message):
        if message.get("type") == "Turn":
            if not message.get("end_of_turn"):
                return
            order = message["turn_order"]
            old = self.items.get(order)
            # Formatted events replace the earlier version of the same turn.
            if old is None or message.get("turn_is_formatted") or not old.get("turn_is_formatted"):
                self.items[order] = message
        elif message.get("type") == "SpeakerRevision":
            for revision in message.get("revisions", []):
                self.revisions[revision["turn_order"]] = revision

    def result(self):
        output = []
        for order, turn in sorted(self.items.items()):
            text = turn["transcript"]
            if not text.strip():
                continue
            revision = self.revisions.get(order, {})
            speaker = revision.get("speaker_label") or turn.get("speaker_label")
            if not speaker:
                # Some responses expose labels only at word level.
                words = revision.get("words", turn.get("words", []))
                labels = {w.get("speaker") for w in words if w.get("speaker")}
                if len(labels) == 1:
                    speaker = labels.pop()
            output.append({"speaker": speaker or "Unknown", "text": text})
        return {"turns": output}


async def stream(audio, audit, key):
    turns = Turns()
    begun = asyncio.Event()
    terminating = False
    uri = "wss://streaming.assemblyai.com/v3/ws?" + urlencode(parameters())
    async with connect(
        uri,
        additional_headers={"Authorization": key, "AssemblyAI-Version": "2025-05-12"},
        open_timeout=20, close_timeout=5, max_size=8 * 1024 * 1024,
    ) as ws:
        async def receive():
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=60)
                # Store the original JSON payload, without parsing/reserializing it.
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8")
                audit.write(raw + "\n")
                audit.flush()
                message = json.loads(raw)
                kind = message.get("type")
                if kind == "Begin":
                    config = message.get("configuration", {})
                    if not (config.get("redact_pii") is True
                            and config.get("speaker_labels") is True
                            and config.get("domain") == "medical-v1"):
                        raise TranscriptionError("Server did not confirm required privacy, speaker, and medical settings.")
                    begun.set()
                elif kind in ("Error", "Warning") or message.get("error"):
                    # Error payloads stay in the audit; never echo transcript text.
                    raise TranscriptionError("AssemblyAI returned an error or warning; see the frames audit.")
                elif kind == "Termination":
                    if not terminating:
                        raise TranscriptionError("AssemblyAI ended the session before all audio was sent.")
                    return
                turns.accept(message)

        async def send():
            nonlocal terminating
            await asyncio.wait_for(begun.wait(), timeout=30)
            loop = asyncio.get_running_loop()
            deadline = loop.time()

            async def send_chunk(chunk):
                nonlocal deadline
                # At most 100 ms per packet; pad a short last packet to >= 50 ms.
                if len(chunk) < 1600:
                    chunk += bytes(1600 - len(chunk))
                await asyncio.sleep(max(0, deadline - loop.time()))
                await ws.send(chunk)
                deadline = max(deadline, loop.time()) + len(chunk) / 32000

            while True:
                chunk = audio.readframes(1600)
                if not chunk:
                    break
                await send_chunk(chunk)
            # Give the endpoint detector real-time silence to finalize the last turn.
            for _ in range(10):
                await send_chunk(bytes(3200))
            await asyncio.sleep(max(0, deadline - loop.time()))
            terminating = True
            await ws.send('{"type":"Terminate"}')

        receiver = asyncio.create_task(receive())
        sender = asyncio.create_task(send())
        try:
            done, _ = await asyncio.wait((receiver, sender), return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
            if receiver in done and not sender.done():
                raise TranscriptionError("Session ended before audio transmission completed.")
            await sender
            await asyncio.wait_for(receiver, timeout=30)
        finally:
            for task in (sender, receiver):
                if not task.done():
                    task.cancel()
            await asyncio.gather(sender, receiver, return_exceptions=True)
    return turns.result()


def write_result(path, result):
    temp = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".transcript-", delete=False) as handle:
            temp = handle.name
            json.dump(result, handle, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
        temp = None
    finally:
        if temp is not None:
            os.unlink(temp)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
    if not key:
        raise TranscriptionError("Set ASSEMBLYAI_API_KEY before running.")
    frames = Path(str(args.output) + ".frames.jsonl")
    if args.audio.resolve() in (args.output.resolve(), frames.resolve()):
        raise TranscriptionError("Output paths must differ from the input audio.")
    with wave.open(str(args.audio), "rb") as audio:
        if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate(), audio.getcomptype()) != (1, 2, 16000, "NONE"):
            raise TranscriptionError("Audio must be an uncompressed 16 kHz mono 16-bit PCM WAV.")
        if audio.getnframes() == 0:
            raise TranscriptionError("Audio is empty.")
        # Remove any old success output so failure cannot leave a stale result.
        args.output.unlink(missing_ok=True)
        with frames.open("w", encoding="utf-8", newline="\n") as audit:
            os.chmod(frames, 0o600)
            result = asyncio.run(stream(audio, audit, key))
        write_result(args.output, result)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Transcription interrupted.", file=sys.stderr)
        sys.exit(130)
    except Exception as exc:
        # Avoid exposing server payloads, personal information, or credentials.
        detail = str(exc) if isinstance(exc, TranscriptionError) else type(exc).__name__
        print(f"Transcription failed: {detail}", file=sys.stderr)
        sys.exit(1)
