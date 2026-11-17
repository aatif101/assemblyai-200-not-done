#!/usr/bin/env python3
"""Stream PCM WAV audio in real time; persist redacted server messages verbatim."""

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
MODEL = "universal-3-6-pro"
# Identity/contact/financial/demographic details. An omitted policy list also
# redacts drugs, conditions, and procedures, removing the clinic's vocabulary.
PII_POLICIES = (
    "account_number", "banking_information", "credit_card_cvv",
    "credit_card_expiration", "credit_card_number", "date_of_birth",
    "drivers_license", "email_address", "healthcare_number", "ip_address",
    "location", "location_address", "location_address_street", "location_city",
    "location_coordinate", "location_country", "location_state", "location_zip",
    "passport_number", "password", "person_age", "person_name", "phone_number",
    "us_social_security_number", "username", "vehicle_id", "url",
    "number_sequence", "gender", "gender_sexuality", "marital_status",
    "nationality", "physical_attribute", "political_affiliation", "religion",
    "sexuality", "occupation",
)
PARAMETERS = {
    "speech_model": MODEL,
    "sample_rate": 16000,
    "encoding": "pcm_s16le",
    "domain": "medical-v1",
    "speaker_labels": "true",
    "speaker_labels_revision_interval_ms": 300000,
    "redact_pii": "true",
    "redact_pii_policies": json.dumps(PII_POLICIES),
    "redact_pii_sub": "entity_name",
    "include_partial_turns": "false",
    # Explicit overrides apply even with the diarization turn profile. Allow
    # pauses inside medical terms and identifiers before finalizing a turn.
    "min_turn_silence": 800,
    "max_turn_silence": 2560,
}


class TranscriptionError(Exception):
    pass


def check_begin(message):
    if message.get("type") != "Begin":
        raise TranscriptionError("Expected session Begin confirmation.")
    config = message.get("configuration", {})
    required = {
        "model": MODEL,
        "domain": "medical-v1",
        "speaker_labels": True,
        "redact_pii": True,
    }
    for field, value in required.items():
        if config.get(field) != value:
            raise TranscriptionError(f"Server did not confirm required setting: {field}.")
    if config.get("include_partial_turns") is True:
        raise TranscriptionError("Server enabled unsafe partial transcripts.")


class Turns:
    def __init__(self):
        self.turns = {}
        self.labels = {}

    def accept(self, message):
        kind = message.get("type")
        if kind == "Turn":
            if message.get("end_of_turn") is not True:
                raise TranscriptionError("Server returned a partial despite partials being disabled.")
            order = message["turn_order"]
            self.turns[order] = {
                "speaker": self.labels.get(order, message.get("speaker_label")) or "UNKNOWN",
                "text": message["transcript"],
            }
        elif kind == "SpeakerRevision":
            for revision in message["revisions"]:
                order = revision["turn_order"]
                self.labels[order] = revision.get("speaker_label") or "UNKNOWN"
                if order in self.turns:
                    self.turns[order]["speaker"] = self.labels[order]
        elif kind == "Error" or message.get("error"):
            # Never echo provider payloads: they may contain call information.
            raise TranscriptionError("AssemblyAI reported an error; see the frames audit log.")

    def result(self):
        return {"turns": [self.turns[k] for k in sorted(self.turns)]}


async def receive(ws, audit):
    raw = await asyncio.wait_for(ws.recv(), timeout=60)
    if not isinstance(raw, str):
        raise TranscriptionError("Expected a JSON text message from AssemblyAI.")
    # Preserve the exact message, including unknown fields and whitespace.
    audit.write(raw + "\n")
    audit.flush()
    message = json.loads(raw)
    if not isinstance(message, dict):
        raise TranscriptionError("Expected a JSON object from AssemblyAI.")
    return message


async def send_audio(ws, audio, state):
    loop = asyncio.get_running_loop()
    # Delay each chunk until its samples would have arrived on a live line.
    # Rebase after delays so a slow connection never causes catch-up bursts.
    deadline = loop.time()
    while chunk := audio.readframes(800):  # 50ms, PCM only (no WAV header)
        deadline = max(deadline, loop.time()) + len(chunk) / 32000
        await asyncio.sleep(max(0, deadline - loop.time()))
        await ws.send(chunk)
    state["terminate_sent"] = True
    await ws.send(json.dumps({"type": "Terminate"}))


async def stream(audio, audit, api_key):
    turns = Turns()
    state = {"terminate_sent": False}
    async with connect(
        ENDPOINT + "?" + urlencode(PARAMETERS),
        additional_headers={"Authorization": api_key},
        open_timeout=30,
        close_timeout=5,
        max_size=8 * 1024 * 1024,
    ) as ws:
        begin = await receive(ws, audit)
        turns.accept(begin)
        check_begin(begin)  # Do not send audio until required features confirmed.

        async def collect():
            while True:
                message = await receive(ws, audit)
                turns.accept(message)
                if message.get("type") == "Termination":
                    if not state["terminate_sent"]:
                        raise TranscriptionError("Session terminated before audio finished.")
                    return

        sender = asyncio.create_task(send_audio(ws, audio, state))
        receiver = asyncio.create_task(collect())
        try:
            # gather propagates either task's failure immediately.
            await asyncio.gather(sender, receiver)
        finally:
            for task in (sender, receiver):
                task.cancel()
            await asyncio.gather(sender, receiver, return_exceptions=True)
    return turns.result()


def write_result(path, result):
    # Publish only after Termination and all final speaker revisions.
    fd, temporary = tempfile.mkstemp(prefix=".transcript-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(result, output, ensure_ascii=False)
            output.write("\n")
        os.replace(temporary, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    try:
        api_key = os.environ.get("ASSEMBLYAI_API_KEY")
        if not api_key:
            raise TranscriptionError("Set ASSEMBLYAI_API_KEY in the environment.")
        frames = Path(str(args.output) + ".frames.jsonl")
        paths = [p.resolve() for p in (args.audio, args.output, frames)]
        if len(set(paths)) != 3:
            raise TranscriptionError("Audio, output, and audit paths must be distinct.")
        with wave.open(str(args.audio), "rb") as audio:
            if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate(), audio.getcomptype()) != (1, 2, 16000, "NONE"):
                raise TranscriptionError("Audio must be a 16 kHz mono 16-bit PCM WAV.")
            if audio.getnframes() == 0:
                raise TranscriptionError("Audio file is empty.")
            # Remove stale results so a failed new attempt cannot look successful.
            args.output.unlink(missing_ok=True)
            fd = os.open(frames, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as audit:
                os.fchmod(audit.fileno(), 0o600)
                result = asyncio.run(stream(audio, audit, api_key))
            write_result(args.output, result)
        print(f"Transcription complete: {len(result['turns'])} turns.", file=sys.stderr)
        return 0
    except KeyboardInterrupt:
        print("Transcription interrupted.", file=sys.stderr)
        return 130
    except Exception as exc:
        # Avoid exception payloads/tracebacks that may expose identifiers or keys.
        detail = str(exc) if isinstance(exc, TranscriptionError) else type(exc).__name__
        print(f"Transcription failed: {detail}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
