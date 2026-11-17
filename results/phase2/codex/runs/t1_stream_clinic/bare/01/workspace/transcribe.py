#!/usr/bin/env python3
"""Real-time WAV ingestion with provider-side medical tuning and PII masking."""
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


# Preserve clinical vocabulary while masking personal and administrative identifiers.
PII_POLICIES = [
    "person_name", "phone_number", "us_social_security_number", "email_address",
    "date_of_birth", "person_age", "location", "account_number",
    "banking_information", "credit_card_number", "credit_card_expiration",
    "credit_card_cvv", "drivers_license", "passport_number", "healthcare_number",
    "ip_address", "username", "password", "url", "vehicle_id", "number_sequence",
    "date", "date_interval", "nationality", "marital_status", "occupation",
    "organization", "organization_medical_facility", "political_affiliation",
    "religion", "gender_sexuality", "physical_attribute",
]


class TranscriptionError(Exception):
    pass


def parameters():
    return {
        "sample_rate": "16000", "encoding": "pcm_s16le",
        "speech_model": "universal-streaming-english", "domain": "medical-v1",
        "speaker_labels": "true", "format_turns": "true",
        "redact_pii": "true", "redact_pii_sub": "entity_name",
        "redact_pii_policies": json.dumps(PII_POLICIES),
        "include_partial_turns": "false", "customer_support_audio_capture": "false",
    }


def accept_turn(turns, event):
    order = event["turn_order"]
    previous = turns.get(order)
    # Never replace a completed/formatted turn with an older partial version.
    if previous and previous.get("end_of_turn") and not event.get("end_of_turn"):
        return
    if previous and previous.get("turn_is_formatted") and not event.get("turn_is_formatted"):
        return
    turns[order] = event


def final_turns(turns):
    result = []
    for _, turn in sorted(turns.items()):
        text = turn.get("transcript", "")
        if not text.strip():
            continue
        if not turn.get("end_of_turn"):
            raise TranscriptionError("Session ended with an unfinished turn")
        # Keep the provider's final, formatted, redacted text verbatim.
        speaker = turn.get("speaker_label")
        if speaker is None:
            labels = {w.get("speaker") for w in turn.get("words", [])
                      if w.get("speaker") is not None}
            if len(labels) == 1:
                speaker = labels.pop()
        result.append({"speaker": str(speaker) if speaker is not None else "unknown",
                       "text": text})
    return {"turns": result}


async def run_session(audio, audit, key):
    turns = {}
    began = asyncio.Event()
    terminated = asyncio.Event()
    uri = "wss://streaming.assemblyai.com/v3/ws?" + urlencode(parameters())
    async with connect(uri, additional_headers={"Authorization": key},
                       open_timeout=20, close_timeout=5, max_size=8 * 1024 * 1024) as ws:
        async def receive():
            async for raw in ws:
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8")
                # Write original JSON, including unknown fields/event types, before parsing.
                audit.write(raw + "\n")
                audit.flush()
                event = json.loads(raw)
                kind = event.get("type")
                if kind == "Begin":
                    config = event.get("configuration", {})
                    if (config.get("redact_pii") is not True
                            or config.get("speaker_labels") is not True
                            or config.get("domain") != "medical-v1"):
                        raise TranscriptionError("Server did not confirm required privacy/medical/speaker settings")
                    began.set()
                elif kind == "Turn":
                    accept_turn(turns, event)
                elif kind == "SpeakerRevision":
                    for revision in event.get("revisions", []):
                        if revision["turn_order"] in turns:
                            turn = turns[revision["turn_order"]]
                            turn["speaker_label"] = revision.get("speaker_label")
                            if "words" in revision:
                                turn["words"] = revision["words"]
                elif kind == "Termination":
                    terminated.set()
                    return
                elif kind == "Error" or "error" in event:
                    # Details remain in audit; never echo possible personal data to logs.
                    raise TranscriptionError("AssemblyAI returned an error; see the audit file")
            raise TranscriptionError("Connection closed before Termination")

        async def send():
            await asyncio.wait_for(began.wait(), timeout=20)
            # 100 ms chunks, paced by a monotonic clock, excluding the WAV header.
            loop = asyncio.get_running_loop()
            deadline = loop.time()
            while chunk := audio.readframes(1600):
                frame_count = len(chunk) // 2
                if frame_count < 800:  # Streaming API requires at least 50 ms.
                    chunk += b"\x00" * (1600 - len(chunk))
                    frame_count = 800
                await ws.send(chunk)
                deadline += frame_count / 16000
                await asyncio.sleep(max(0, deadline - loop.time()))
            # Explicitly close the last utterance, then drain all final messages.
            await ws.send(json.dumps({"type": "ForceEndpoint"}))
            await ws.send(json.dumps({"type": "Terminate"}))
            await asyncio.wait_for(terminated.wait(), timeout=60)

        # A receiver error must interrupt pacing immediately, and vice versa.
        async with asyncio.TaskGroup() as group:
            group.create_task(receive())
            group.create_task(send())
    return final_turns(turns)


def write_result(path, result):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".transcript-", delete=False) as f:
            temporary = f.name
            json.dump(result, f, ensure_ascii=False)
            f.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
    if not key:
        raise TranscriptionError("ASSEMBLYAI_API_KEY is required")
    audit_path = Path(str(args.output) + ".frames.jsonl")
    paths = [p.resolve() for p in (args.audio, args.output, audit_path)]
    if len(set(paths)) != 3:
        raise TranscriptionError("Audio, output, and audit paths must be distinct")
    with wave.open(str(args.audio), "rb") as audio:
        if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate(),
                audio.getcomptype()) != (1, 2, 16000, "NONE"):
            raise TranscriptionError("Input must be uncompressed 16 kHz mono 16-bit PCM WAV")
        if not audio.getnframes():
            raise TranscriptionError("Input WAV is empty")
        # Restrictive permissions apply even when replacing an existing audit file.
        fd = os.open(audit_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as audit:
            result = asyncio.run(run_session(audio, audit, key))
        write_result(args.output, result)
    print(f"Saved {len(result['turns'])} turns.", file=sys.stderr)


if __name__ == "__main__":
    try:
        main()
    except (Exception, KeyboardInterrupt) as error:
        # Exception strings from third-party libraries may contain response text.
        def safe_reason(exc):
            if isinstance(exc, TranscriptionError):
                return str(exc)
            if isinstance(exc, BaseExceptionGroup):
                for child in exc.exceptions:
                    if reason := safe_reason(child):
                        return reason
            return None

        if reason := safe_reason(error):
            print(f"Transcription failed: {reason}", file=sys.stderr)
        else:
            print(f"Transcription failed ({type(error).__name__}). No successful result was written.",
                  file=sys.stderr)
        sys.exit(1)
