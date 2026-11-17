"""Stream PCM WAV audio to AssemblyAI without persisting unredacted transcripts."""

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


SAMPLE_RATE = 16000
CHUNK_FRAMES = 1600  # 100 ms, inside AssemblyAI's 50–1000 ms range.
FINAL_TIMEOUT = 60

# Preserve clinical vocabulary while removing personal identifiers. The complete
# list is explicit so that server default policies cannot remove medications,
# conditions, or procedures from the clinical transcript.
PII_POLICIES = [
    "person_name", "phone_number", "us_social_security_number",
    "date_of_birth", "person_age", "email_address", "location",
    "healthcare_number", "account_number", "banking_information",
    "credit_card_number", "credit_card_expiration", "credit_card_cvv",
    "drivers_license", "passport_number", "ip_address", "url",
    "username", "password", "vehicle_id", "number_sequence", "date",
]


class TranscriptionError(Exception):
    pass


def session_url():
    return "wss://streaming.assemblyai.com/v3/ws?" + urlencode({
        "sample_rate": SAMPLE_RATE,
        "encoding": "pcm_s16le",
        "speech_model": "u3-rt-pro",
        "mode": "max_accuracy",
        "domain": "medical-v1",
        "speaker_labels": "true",
        "format_turns": "true",
        "redact_pii": "true",
        "redact_pii_policies": json.dumps(PII_POLICIES),
        "redact_pii_sub": "entity_name",
        # Redaction needs the full turn's context. Never request raw partials.
        "include_partial_turns": "false",
        "customer_support_audio_capture": "false",
    })


class Turns:
    def __init__(self):
        self.final = {}
        self.revisions = {}

    def receive(self, message):
        if message.get("type") == "Turn" and message.get("end_of_turn"):
            order = message["turn_order"]
            previous = self.final.get(order)
            # Formatting may arrive as a second event for the same turn.
            if not previous or message.get("turn_is_formatted") or not previous.get("turn_is_formatted"):
                self.final[order] = message
        elif message.get("type") == "SpeakerRevision":
            for revision in message["revisions"]:
                self.revisions[revision["turn_order"]] = revision

    def output(self):
        result = []
        for order, turn in sorted(self.final.items()):
            if not turn["transcript"].strip():
                continue
            speaker = self.revisions.get(order, turn).get("speaker_label")
            result.append({"speaker": speaker or "UNKNOWN", "text": turn["transcript"]})
        return {"turns": result}


async def read_message(ws, audit):
    raw = await ws.recv()
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    # Store the actual wire payload, not a reserialization or SDK model. Flush
    # before handling it, including errors, unknown events, and Termination.
    audit.write(raw + "\n")
    audit.flush()
    message = json.loads(raw)
    if not isinstance(message, dict):
        raise TranscriptionError("AssemblyAI returned an invalid event.")
    if message.get("type") == "Error" or "error" in message:
        # Server error text may contain sensitive content; only the raw audit
        # receives it, as requested. Do not copy it to console logs.
        raise TranscriptionError("AssemblyAI reported an error; see the frames audit.")
    if message.get("type") == "Warning":
        raise TranscriptionError("AssemblyAI reported a configuration warning; see the frames audit.")
    return message


async def send_audio(ws, audio):
    loop = asyncio.get_running_loop()
    deadline = loop.time()
    samples_sent = 0
    while True:
        data = audio.readframes(CHUNK_FRAMES)
        if not data:
            break
        if len(data) % 2:
            raise TranscriptionError("Audio contains an incomplete PCM sample.")
        samples_sent += len(data) // 2
        # Schedule by actual samples using a monotonic clock. Waiting before
        # sending also paces the last chunk and never sends ahead of real time.
        deadline = max(deadline, loop.time()) + len(data) / (SAMPLE_RATE * 2)
        await asyncio.sleep(max(0, deadline - loop.time()))
        # Pad only a final chunk shorter than the API's minimum 50 ms.
        if len(data) < SAMPLE_RATE * 2 // 20:
            data += b"\0" * (SAMPLE_RATE * 2 // 20 - len(data))
        await ws.send(data)
    if samples_sent != audio.getnframes():
        raise TranscriptionError("Audio is truncated.")
    await ws.send(json.dumps({"type": "Terminate"}))


async def receive_turns(ws, audit, turns):
    while True:
        message = await read_message(ws, audit)
        turns.receive(message)
        if message.get("type") == "Termination":
            return


async def stream(audio, audit, key):
    turns = Turns()
    async with connect(
        session_url(), additional_headers={"Authorization": key,
                                           "AssemblyAI-Version": "2025-05-12"},
        open_timeout=20, close_timeout=5, max_size=8 * 1024 * 1024,
    ) as ws:
        begin = await asyncio.wait_for(read_message(ws, audit), 30)
        if begin.get("type") != "Begin":
            raise TranscriptionError("AssemblyAI did not start the session.")
        configuration = begin.get("configuration", {})
        if (configuration.get("redact_pii") is not True
                or configuration.get("speaker_labels") is not True
                or configuration.get("domain") != "medical-v1"):
            raise TranscriptionError("AssemblyAI did not confirm medical, speaker, and redaction settings.")
        sender = asyncio.create_task(send_audio(ws, audio))
        receiver = asyncio.create_task(receive_turns(ws, audit, turns))
        try:
            done, _ = await asyncio.wait({sender, receiver}, return_when=asyncio.FIRST_COMPLETED)
            if receiver in done:
                receiver.result()
                # A Termination before all audio was sent is a failed call.
                if not sender.done():
                    raise TranscriptionError("AssemblyAI terminated before all audio was sent.")
            await sender
            # Keep receiving through Termination, including final formatting
            # and diarization revisions sent after the last audio chunk.
            await asyncio.wait_for(receiver, FINAL_TIMEOUT)
        finally:
            for task in (sender, receiver):
                if not task.done():
                    task.cancel()
            await asyncio.gather(sender, receiver, return_exceptions=True)
    return turns.output()


def write_output(path, result):
    fd, temporary = tempfile.mkstemp(prefix=".transcript-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(result, output, ensure_ascii=False)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    audit_path = Path(str(args.output) + ".frames.jsonl")
    if len({args.audio.resolve(), args.output.resolve(), audit_path.resolve()}) != 3:
        raise TranscriptionError("Input, output, and audit paths must be different.")
    for destination in (args.output, audit_path):
        if destination.exists() and os.path.samefile(args.audio, destination):
            raise TranscriptionError("An output path refers to the input audio.")
    key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
    if not key:
        raise TranscriptionError("Set ASSEMBLYAI_API_KEY in the environment.")
    with wave.open(str(args.audio), "rb") as audio:
        if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate(), audio.getcomptype()) != (1, 2, SAMPLE_RATE, "NONE"):
            raise TranscriptionError("Audio must be uncompressed 16 kHz mono 16-bit PCM WAV.")
        if not audio.getnframes():
            raise TranscriptionError("Audio is empty.")
        # Restrict local transcript/audit access, including an existing file.
        fd = os.open(audit_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as audit:
            os.fchmod(audit.fileno(), 0o600)
            result = asyncio.run(stream(audio, audit, key))
        write_output(args.output, result)
    print(f"Transcription complete: {len(result['turns'])} turns.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Transcription interrupted.", file=sys.stderr)
        sys.exit(130)
    except TranscriptionError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        # Do not print exception contents: transport exceptions can include
        # headers or server data. The exception type is sufficient to triage.
        print(f"Transcription failed ({type(exc).__name__}).", file=sys.stderr)
        sys.exit(1)
