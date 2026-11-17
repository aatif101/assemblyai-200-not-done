#!/usr/bin/env python3
"""Paced WAV -> AssemblyAI streaming -> redacted turns and exact wire audit."""

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


# Keep clinical vocabulary, but mask identifying personal and financial details.
PII_POLICIES = [
    "person_name", "phone_number", "us_social_security_number", "date_of_birth",
    "person_age", "date", "email_address", "location", "account_number",
    "healthcare_number", "banking_information", "credit_card_number",
    "credit_card_expiration", "credit_card_cvv", "drivers_license",
    "passport_number", "ip_address", "password", "username", "url", "vehicle_id",
]


class TranscriptionError(Exception):
    pass


class Turns:
    def __init__(self):
        self.final = {}
        self.pending = set()

    def accept(self, event):
        if event.get("type") == "Turn":
            order = event["turn_order"]
            if not event.get("end_of_turn"):
                if event.get("transcript"):
                    self.pending.add(order)
                return
            self.pending.discard(order)
            previous = self.final.get(order)
            # A formatted event replaces the earlier unformatted final.
            if previous and previous.get("turn_is_formatted") and not event.get("turn_is_formatted"):
                return
            self.final[order] = event
        elif event.get("type") == "SpeakerRevision":
            for revision in event.get("revisions", []):
                previous = self.final.get(revision["turn_order"])
                if previous is not None:
                    if "speaker_label" in revision:
                        previous["speaker_label"] = revision["speaker_label"]
                    # Speaker corrections must not replace already-redacted text.
                    revised = {(w["start"], w["end"]): w.get("speaker")
                               for w in revision.get("words", [])}
                    for word in previous.get("words", []):
                        key = (word["start"], word["end"])
                        if key in revised:
                            word["speaker"] = revised[key]

    def result(self):
        if self.pending:
            raise TranscriptionError("Session ended with unfinished transcript turns")
        turns = []
        for _, event in sorted(self.final.items()):
            text = event["transcript"]
            if not text.strip():
                continue
            turns.extend(speaker_turns(event))
        return {"turns": turns}


def speaker_turns(event):
    """Split a final API turn on actual word-level speaker changes.

    Slice only the redacted final transcript; never reconstruct it from a
    different text field. This also preserves final formatting and punctuation.
    """
    text = event["transcript"]
    words = event.get("words", [])
    fallback = event.get("speaker_label") or "Unknown"
    labels = [str(w.get("speaker") or fallback) for w in words]
    if len(set(labels)) <= 1:
        return [{"speaker": labels[0] if labels else str(fallback), "text": text}]

    starts = []
    cursor = 0
    for word in words:
        token = word["text"]
        start = text.find(token, cursor)
        if not token or start < 0 or any(c.isalnum() for c in text[cursor:start]):
            raise TranscriptionError("Cannot align speaker labels with the final redacted transcript")
        starts.append(start)
        cursor = start + len(token)
    if any(c.isalnum() for c in text[cursor:]):
        raise TranscriptionError("Incomplete word labels for the final redacted transcript")
    result = []
    beginning = 0
    speaker = labels[0]
    for index in range(1, len(words)):
        if labels[index] != speaker:
            result.append({"speaker": speaker, "text": text[beginning:starts[index]].strip()})
            beginning = starts[index]
            speaker = labels[index]
    result.append({"speaker": speaker, "text": text[beginning:].strip()})
    return result


def parameters():
    return {
        "sample_rate": 16000,
        "encoding": "pcm_s16le",
        "speech_model": "u3-rt-pro",
        "mode": "max_accuracy",
        "domain": "medical-v1",
        "speaker_labels": "true",
        "redact_pii": "true",
        "redact_pii_policies": json.dumps(PII_POLICIES, separators=(",", ":")),
        "redact_pii_sub": "entity_name",
        # Do not request speculative, potentially identifying partial text.
        "include_partial_turns": "false",
        "format_turns": "true",
    }


async def send_audio(ws, audio):
    loop = asyncio.get_running_loop()
    started = loop.time()
    samples_sent = 0
    source_samples = 0
    while True:
        pcm = audio.readframes(1600)  # 100 ms, WAV container is never transmitted.
        if not pcm:
            break
        if len(pcm) % 2:
            raise TranscriptionError("WAV contains an incomplete PCM sample")
        source_samples += len(pcm) // 2
        # The last packet must also meet the API's 50 ms minimum.
        if len(pcm) < 1600:
            pcm += b"\x00" * (1600 - len(pcm))
        samples_sent += len(pcm) // 2
        # Pace against a monotonic clock, including the duration of the final packet.
        await asyncio.sleep(max(0, started + samples_sent / 16000 - loop.time()))
        await ws.send(pcm)
    if source_samples != audio.getnframes():
        raise TranscriptionError("WAV audio is truncated")
    await ws.send('{"type":"ForceEndpoint"}')
    await ws.send('{"type":"Terminate"}')


async def transcribe(audio, audit, key):
    turns = Turns()

    async def receive_one(ws):
        raw = await ws.recv()
        if not isinstance(raw, str):
            raise TranscriptionError("AssemblyAI returned a non-JSON text message")
        # Write the original JSON payload, without parsing/reserializing or filtering.
        audit.write(raw + "\n")
        audit.flush()
        event = json.loads(raw)
        if not isinstance(event, dict):
            raise TranscriptionError("AssemblyAI returned a non-object message")
        if event.get("type") == "Error" or event.get("error"):
            # Do not echo server content, which may contain identifying information.
            raise TranscriptionError("AssemblyAI reported an error; see the audit log")
        if event.get("type") == "Warning":
            raise TranscriptionError("AssemblyAI reported a configuration warning; see the audit log")
        return event

    async def receive_until_termination(ws):
        while True:
            event = await receive_one(ws)
            turns.accept(event)
            if event.get("type") == "Termination":
                return

    url = "wss://streaming.assemblyai.com/v3/ws?" + urlencode(parameters())
    async with connect(
        url, additional_headers={"Authorization": key}, open_timeout=20,
        close_timeout=5, max_size=16 * 1024 * 1024,
    ) as ws:
        begin = await asyncio.wait_for(receive_one(ws), timeout=20)
        if begin.get("type") != "Begin":
            raise TranscriptionError("AssemblyAI did not begin the session")
        config = begin.get("configuration", {})
        if config.get("redact_pii") is not True:
            raise TranscriptionError("AssemblyAI did not confirm PII redaction; no audio sent")
        if config.get("speaker_labels") is not True or config.get("domain") != "medical-v1":
            raise TranscriptionError("AssemblyAI did not confirm medical mode and speaker labels; no audio sent")
        sender = asyncio.create_task(send_audio(ws, audio))
        receiver = asyncio.create_task(receive_until_termination(ws))
        try:
            done, _ = await asyncio.wait({sender, receiver}, return_when=asyncio.FIRST_COMPLETED)
            if receiver in done and not sender.done():
                await receiver  # Propagate an API error before reporting early termination.
                raise TranscriptionError("AssemblyAI terminated before all audio was sent")
            await sender
            await asyncio.wait_for(receiver, timeout=45)
        finally:
            for task in (sender, receiver):
                if not task.done():
                    task.cancel()
            await asyncio.gather(sender, receiver, return_exceptions=True)
    return turns.result()


def write_result(path, result):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".transcript-", delete=False) as out:
            temporary = out.name
            json.dump(result, out, ensure_ascii=False)
            out.write("\n")
            out.flush()
            os.fsync(out.fileno())
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
    paths = [args.audio.resolve(), args.output.resolve(), audit_path.resolve()]
    if len(set(paths)) != 3:
        raise TranscriptionError("Input, output, and audit paths must be different")
    for output in (args.output, audit_path):
        if output.exists() and os.path.samefile(args.audio, output):
            raise TranscriptionError("Output must not overwrite the input audio")
    if args.output.exists() and audit_path.exists() and os.path.samefile(args.output, audit_path):
        raise TranscriptionError("Transcript and audit paths must be different files")
    with wave.open(str(args.audio), "rb") as audio:
        if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate(), audio.getcomptype()) != (1, 2, 16000, "NONE"):
            raise TranscriptionError("Audio must be a 16 kHz mono, signed 16-bit PCM WAV")
        if audio.getnframes() == 0:
            raise TranscriptionError("Audio file is empty")
        fd = os.open(audit_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as audit:
            result = asyncio.run(transcribe(audio, audit, key))
        write_result(args.output, result)
    print(f"Transcribed {len(result['turns'])} turns.", file=sys.stderr)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Transcription interrupted", file=sys.stderr)
        sys.exit(130)
    except Exception as exc:
        # Generic library exceptions may include response bodies or credentials.
        message = str(exc) if isinstance(exc, TranscriptionError) else type(exc).__name__
        print(f"Transcription failed: {message}", file=sys.stderr)
        sys.exit(1)
