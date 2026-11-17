"""Paced WAV -> AssemblyAI streaming v3, with server-side PII redaction."""

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


ENDPOINT = "wss://streaming.assemblyai.com/v3/ws"
# Preserve clinical vocabulary; redact identifying details rather than drugs,
# medical conditions, or procedures. These are streaming PII policy names.
PII_POLICIES = [
    "person_name", "phone_number", "us_social_security_number", "email_address",
    "location", "date_of_birth", "person_age", "account_number",
    "banking_information", "credit_card_number", "credit_card_expiration",
    "credit_card_cvv", "drivers_license", "passport_number", "healthcare_number",
    "ip_address", "url", "username", "password", "vehicle_id",
]
PARAMETERS = {
    "sample_rate": 16000,
    "encoding": "pcm_s16le",
    "speech_model": "u3-rt-pro",
    "domain": "medical-v1",
    "mode": "max_accuracy",
    "speaker_labels": "true",
    "redact_pii": "true",
    "redact_pii_policies": json.dumps(PII_POLICIES),
    "redact_pii_sub": "entity_name",
    # Redact before delivery; never request potentially unredacted partials.
    "include_partial_turns": "false",
    "format_turns": "true",
    "customer_support_audio_capture": "false",
}
BEGIN_TIMEOUT = 20
RECEIVE_TIMEOUT = 90
CHUNK_FRAMES = 1600  # 100 ms at 16 kHz, signed 16-bit little-endian PCM.


class TranscriptionError(Exception):
    """Safe, fixed diagnostic: never includes transcript or server payloads."""


class Turns:
    def __init__(self):
        self.final = {}

    def receive(self, event):
        kind = event.get("type")
        if kind == "Turn":
            order = event.get("turn_order")
            if not isinstance(order, int) or not isinstance(event.get("transcript"), str):
                raise TranscriptionError("AssemblyAI returned an invalid turn.")
            if event.get("end_of_turn") is True:
                previous = self.final.get(order)
                # A formatted final replaces its earlier unformatted final.
                if not previous or not previous.get("turn_is_formatted") or event.get("turn_is_formatted"):
                    self.final[order] = event
        elif kind == "SpeakerRevision":
            for revision in event.get("revisions", []):
                previous = self.final.get(revision.get("turn_order"))
                if previous is not None:
                    for key in ("speaker_label", "words"):
                        if key in revision:
                            previous[key] = revision[key]

    def output(self):
        result = []
        for _, event in sorted(self.final.items()):
            text = event["transcript"]
            if not text.strip():
                continue
            speaker = event.get("speaker_label")
            if not speaker:
                labels = {w.get("speaker") for w in event.get("words", []) if w.get("speaker")}
                speaker = next(iter(labels)) if len(labels) == 1 else "UNKNOWN"
            result.append({"speaker": str(speaker), "text": text})
        return {"turns": result}


def check_configuration(event):
    config = event.get("configuration")
    # Require confirmation before sending any patient audio. Fail rather than
    # silently continue if the service cannot confirm the requested features.
    if not isinstance(config, dict) or any((
        config.get("redact_pii") is not True,
        config.get("speaker_labels") is not True,
        config.get("domain") != "medical-v1",
    )):
        raise TranscriptionError("AssemblyAI did not confirm medical mode, speaker labels, and PII redaction; no audio sent.")


async def send_audio(ws, audio):
    loop = asyncio.get_running_loop()
    started = loop.time()
    sent_frames = 0
    while chunk := audio.readframes(CHUNK_FRAMES):
        sent_frames += len(chunk) // 2
        # Wait until this chunk would have arrived from a live source. Absolute
        # deadlines avoid accumulating timer drift over a long call.
        await asyncio.sleep(max(0, started + sent_frames / 16000 - loop.time()))
        await ws.send(chunk)
    await ws.send('{"type":"Terminate"}')


async def stream(audio, audit, api_key):
    turns = Turns()
    sender = None
    begun = False
    try:
        async with connect(
            ENDPOINT + "?" + urlencode(PARAMETERS),
            additional_headers={"Authorization": api_key, "AssemblyAI-Version": "2025-05-12"},
            open_timeout=BEGIN_TIMEOUT, close_timeout=5, max_size=8 * 1024 * 1024,
        ) as ws:
            while True:
                raw = await asyncio.wait_for(ws.recv(), RECEIVE_TIMEOUT if begun else BEGIN_TIMEOUT)
                # Save the exact JSON message bytes, without model conversion,
                # field removal, reserialization, or locally applied redaction.
                encoded = raw.encode("utf-8") if isinstance(raw, str) else raw
                audit.write(encoded + b"\n")
                audit.flush()
                event = json.loads(encoded)
                if not isinstance(event, dict) or b"\n" in encoded or b"\r" in encoded:
                    raise TranscriptionError("AssemblyAI returned a message incompatible with JSONL audit logging.")
                kind = event.get("type")
                if kind == "Begin":
                    if begun:
                        raise TranscriptionError("AssemblyAI began the session twice.")
                    check_configuration(event)
                    begun = True
                    sender = asyncio.create_task(send_audio(ws, audio))
                elif kind in ("Error", "Warning") or event.get("error"):
                    # Even a warning may mean a privacy/accuracy option was
                    # ignored. Details are preserved only in the requested log.
                    raise TranscriptionError("AssemblyAI reported an error or warning; see the frames audit log.")
                elif not begun:
                    raise TranscriptionError("AssemblyAI sent data before confirming the session.")
                elif kind == "Termination":
                    if not sender.done():
                        raise TranscriptionError("AssemblyAI terminated before all audio was sent.")
                    await sender
                    return turns.output()
                else:
                    turns.receive(event)
                if sender is not None and sender.done():
                    # Surface upload failures promptly; continue receiving final
                    # turns after a successful Terminate until Termination.
                    await sender
    finally:
        if sender is not None:
            if not sender.done():
                sender.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await sender


def different_paths(paths):
    for i, left in enumerate(paths):
        for right in paths[i + 1:]:
            if left.resolve() == right.resolve() or (
                left.exists() and right.exists() and left.samefile(right)
            ):
                raise TranscriptionError("Audio, transcript, and audit log must be different files.")


def atomic_json(path, value):
    fd, name = tempfile.mkstemp(prefix=".transcript-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(value, output, ensure_ascii=False)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(name, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    audit_path = Path(str(args.output) + ".frames.jsonl")
    try:
        api_key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
        if not api_key:
            raise TranscriptionError("Set ASSEMBLYAI_API_KEY in the environment.")
        different_paths([args.audio, args.output, audit_path])
        with wave.open(str(args.audio), "rb") as audio:
            if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate(), audio.getcomptype()) != (1, 2, 16000, "NONE"):
                raise TranscriptionError("Audio must be uncompressed 16 kHz mono, 16-bit PCM WAV.")
            if audio.getnframes() == 0:
                raise TranscriptionError("Audio is empty.")
            # Refuse audit symlinks; use private file permissions even on reruns.
            fd = os.open(audit_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "wb") as audit:
                os.fchmod(audit.fileno(), 0o600)
                result = asyncio.run(stream(audio, audit, api_key))
                os.fsync(audit.fileno())
        atomic_json(args.output, result)
        print(f"Transcription complete: {len(result['turns'])} turns.", file=sys.stderr)
        return 0
    except KeyboardInterrupt:
        print("Transcription interrupted.", file=sys.stderr)
        return 130
    except TranscriptionError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception as exc:
        # Library exception messages can contain server text or request headers.
        print(f"Transcription failed ({type(exc).__name__}); no new final transcript was written.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
