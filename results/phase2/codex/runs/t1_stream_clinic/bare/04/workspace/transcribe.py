"""Stream PCM WAV audio to AssemblyAI without saving unredacted transcripts."""

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


# Keep clinical vocabulary, but mask identifying details before they reach us.
PII_POLICIES = [
    "person_name", "phone_number", "us_social_security_number", "email_address",
    "date_of_birth", "person_age", "location", "healthcare_number",
    "account_number", "drivers_license", "passport_number", "credit_card_number",
    "credit_card_expiration", "credit_card_cvv", "banking_information",
    "number_sequence", "username", "password", "ip_address", "url", "vehicle_id",
]


class TranscriptionError(Exception):
    pass


def connection_url():
    params = {
        "sample_rate": 16000,
        "encoding": "pcm_s16le",
        "speech_model": "u3-rt-pro",
        "domain": "medical-v1",
        "mode": "max_accuracy",
        "speaker_labels": "true",
        "redact_pii": "true",
        "redact_pii_policies": json.dumps(PII_POLICIES),
        "redact_pii_sub": "entity_name",
        "include_partial_turns": "false",
        "format_turns": "true",
        "customer_support_audio_capture": "false",
    }
    return "wss://streaming.assemblyai.com/v3/ws?" + urlencode(params)


class Turns:
    def __init__(self):
        self.turns = {}
        self.revisions = {}

    def accept(self, event):
        if event.get("type") == "Turn" and event.get("end_of_turn"):
            order = event["turn_order"]
            previous = self.turns.get(order)
            # Formatting is a replacement, never an additional utterance.
            if not previous or not previous.get("turn_is_formatted") or event.get("turn_is_formatted"):
                self.turns[order] = event
        elif event.get("type") == "SpeakerRevision":
            for revision in event.get("revisions", []):
                self.revisions[revision["turn_order"]] = revision

    def result(self):
        result = []
        for order, event in sorted(self.turns.items()):
            text = event.get("transcript", "")
            if not text.strip():
                continue
            attribution = self.revisions.get(order, event)
            speaker = attribution.get("speaker_label")
            if speaker is None:
                labels = {w.get("speaker") for w in attribution.get("words", [])}
                labels.discard(None)
                speaker = next(iter(labels)) if len(labels) == 1 else "Unknown"
            result.append({"speaker": str(speaker), "text": text})
        return {"turns": result}


async def stream(audio, audit, key):
    turns = Turns()
    began = asyncio.Event()
    terminating = asyncio.Event()

    async with connect(
        connection_url(), additional_headers={"Authorization": key},
        open_timeout=20, close_timeout=5, max_size=8 * 1024 * 1024,
    ) as ws:
        async def receive():
            async for raw in ws:
                if not isinstance(raw, str):
                    raise TranscriptionError("Unexpected binary message from AssemblyAI")
                # Validate without reserializing: the audit record is the original payload.
                event = json.loads(raw)
                if not isinstance(event, dict) or "\n" in raw or "\r" in raw:
                    raise TranscriptionError("AssemblyAI message is not a single-line JSON object")
                audit.write(raw + "\n")
                audit.flush()
                kind = event.get("type")
                if kind in ("Error", "Warning") or event.get("error"):
                    # Do not echo server text, which might contain transcript material.
                    raise TranscriptionError("AssemblyAI reported an error or warning; see the audit log")
                if kind == "Begin":
                    config = event.get("configuration", {})
                    if (config.get("redact_pii") is not True
                            or config.get("speaker_labels") is not True
                            or config.get("domain") != "medical-v1"):
                        raise TranscriptionError("AssemblyAI did not confirm required session features")
                    began.set()
                turns.accept(event)
                if kind == "Termination":
                    if not terminating.is_set():
                        raise TranscriptionError("AssemblyAI terminated before all audio was sent")
                    return
            raise TranscriptionError("AssemblyAI closed without confirming termination")

        async def send():
            await asyncio.wait_for(began.wait(), timeout=20)
            clock = asyncio.get_running_loop()
            deadline = clock.time()
            while True:
                chunk = audio.readframes(1600)  # 100 ms of 16-bit, mono PCM
                if not chunk:
                    break
                # Pad the final chunk to meet the API's minimum chunk duration.
                chunk = chunk.ljust(3200, b"\0")
                deadline = max(deadline, clock.time()) + 0.1
                await asyncio.sleep(max(0, deadline - clock.time()))
                await ws.send(chunk)
            terminating.set()
            await ws.send('{"type":"Terminate"}')

        receiver = asyncio.create_task(receive())
        sender = asyncio.create_task(send())
        try:
            # Surface receiver failures immediately, including during audio delivery.
            done, _ = await asyncio.wait((receiver, sender), return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
            if receiver in done and not sender.done():
                raise TranscriptionError("Session ended before the sender finished")
            await sender
            await asyncio.wait_for(receiver, timeout=30)
        finally:
            for task in (sender, receiver):
                if not task.done():
                    task.cancel()
            await asyncio.gather(sender, receiver, return_exceptions=True)
    return turns.result()


def write_result(path, result):
    # An interrupted/failed run never publishes a partial success JSON file.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".transcript-", delete=False) as out:
            temporary = Path(out.name)
            json.dump(result, out, ensure_ascii=False)
            out.write("\n")
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
    if not key:
        raise TranscriptionError("ASSEMBLYAI_API_KEY is required")
    frames = Path(str(args.output) + ".frames.jsonl")
    if args.audio.resolve() in (args.output.resolve(), frames.resolve()):
        raise TranscriptionError("Output paths must differ from the input audio")
    with wave.open(str(args.audio), "rb") as audio:
        if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate(), audio.getcomptype()) != (1, 2, 16000, "NONE"):
            raise TranscriptionError("Input must be uncompressed 16-bit, 16 kHz mono PCM WAV")
        if not audio.getnframes():
            raise TranscriptionError("Input audio is empty")
        fd = os.open(frames, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as audit:
            result = asyncio.run(stream(audio, audit, key))
        write_result(args.output, result)
    print(f"Saved {len(result['turns'])} turns.", file=sys.stderr)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Transcription interrupted.", file=sys.stderr)
        sys.exit(130)
    except Exception as exc:
        # Third-party exceptions may include headers or text; only show our own messages.
        message = str(exc) if isinstance(exc, TranscriptionError) else type(exc).__name__
        print(f"Transcription failed: {message}", file=sys.stderr)
        sys.exit(1)
