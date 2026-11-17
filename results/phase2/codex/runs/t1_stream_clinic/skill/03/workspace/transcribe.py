"""Replay PCM WAV audio over AssemblyAI v3; persist only server-redacted turns."""

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
# Identity/contact/financial details. Keep medication, condition and procedure
# vocabulary: the service's default 'all' policies would redact those too.
PII_POLICIES = (
    "person_name,phone_number,email_address,us_social_security_number,"
    "date_of_birth,person_age,location,location_address,location_address_street,"
    "location_city,location_state,location_country,location_zip,location_coordinate,"
    "healthcare_number,account_number,banking_information,credit_card_number,"
    "credit_card_cvv,credit_card_expiration,drivers_license,passport_number,"
    "password,username,ip_address,url,vehicle_id,number_sequence,date"
)


class SessionError(Exception):
    pass


def connection_url(rate):
    return ENDPOINT + "?" + urlencode({
        "speech_model": MODEL,
        "sample_rate": rate,
        "encoding": "pcm_s16le",
        "domain": "medical-v1",
        "speaker_labels": "true",
        "speaker_labels_revision_interval_ms": 300000,
        "redact_pii": "true",
        "redact_pii_policies": json.dumps(PII_POLICIES.split(",")),
        "redact_pii_sub": "entity_name",
        "include_partial_turns": "false",
        # Keep the model's dedicated diarization turn-detection profile. Longer
        # silence windows can combine the two sides of a phone conversation.
    })


class Collector:
    def __init__(self):
        self.turns = {}
        self.labels = {}

    def accept(self, message):
        kind = message.get("type")
        if kind == "Error" or message.get("error"):
            # Do not echo arbitrary service text (which may contain identifiers).
            raise SessionError("AssemblyAI reported an error; see the frames log.")
        if kind == "Turn":
            if message.get("end_of_turn") is not True:
                raise SessionError("Unexpected partial turn despite partials being disabled.")
            order = message["turn_order"]
            self.turns[order] = {
                "speaker": self.labels.get(order, message.get("speaker_label")) or "UNKNOWN",
                "text": message["transcript"],
            }
        elif kind == "SpeakerRevision":
            for revision in message["revisions"]:
                order = revision["turn_order"]
                label = revision.get("speaker_label") or "UNKNOWN"
                self.labels[order] = label
                if order in self.turns:
                    self.turns[order]["speaker"] = label

    def result(self):
        return {"turns": [self.turns[key] for key in sorted(self.turns)]}


async def receive_message(ws, audit):
    raw = await ws.recv()
    # Preserve the actual server payload, including unknown fields and key order;
    # don't serialize a typed SDK event or modify/redact the received message.
    payload = raw.encode("utf-8") if isinstance(raw, str) else raw
    audit.write(payload + b"\n")
    audit.flush()
    message = json.loads(payload)
    if not isinstance(message, dict):
        raise SessionError("Expected a JSON object from AssemblyAI.")
    return message


def verify_begin(message):
    if message.get("type") != "Begin":
        raise SessionError("AssemblyAI did not begin the session; see the frames log.")
    config = message.get("configuration", {})
    expected = {"model": MODEL, "domain": "medical-v1",
                "speaker_labels": True, "redact_pii": True}
    if any(config.get(key) != value for key, value in expected.items()):
        raise SessionError("Server did not confirm the requested model, Medical Mode, "
                           "speaker labels and PII redaction. No audio was sent.")


async def send_audio(ws, audio):
    rate = audio.getframerate()
    chunk_frames = rate // 20  # 50 ms; send PCM samples, never the WAV header.
    loop = asyncio.get_running_loop()
    deadline = loop.time()
    while True:
        chunk = audio.readframes(chunk_frames)
        if not chunk:
            break
        # Wait for each chunk's audio duration before sending. Reset when late
        # rather than catching up in a burst after a network/scheduling stall.
        deadline = max(deadline, loop.time()) + len(chunk) / (rate * 2)
        await asyncio.sleep(max(0, deadline - loop.time()))
        await ws.send(chunk)
    await ws.send(json.dumps({"type": "ForceEndpoint"}))
    await ws.send(json.dumps({"type": "Terminate"}))


async def receive_remaining(ws, audit, collector):
    while True:
        message = await receive_message(ws, audit)
        collector.accept(message)
        if message.get("type") == "Termination":
            return


async def stream(audio, audit, api_key):
    collector = Collector()
    async with connect(connection_url(audio.getframerate()),
                       additional_headers={"Authorization": api_key},
                       open_timeout=20, close_timeout=5, max_size=4 * 1024 * 1024) as ws:
        first = await asyncio.wait_for(receive_message(ws, audit), timeout=30)
        verify_begin(first)
        receiver = asyncio.create_task(receive_remaining(ws, audit, collector))
        sender = asyncio.create_task(send_audio(ws, audio))
        try:
            done, _ = await asyncio.wait({sender, receiver},
                                         return_when=asyncio.FIRST_COMPLETED)
            if receiver in done:
                await receiver  # Propagate errors and unexpected disconnects.
                if not sender.done():
                    raise SessionError("Session terminated before all audio was sent.")
            await sender
            # Drain final turns and every speaker revision through Termination.
            await asyncio.wait_for(receiver, timeout=60)
        finally:
            for task in (sender, receiver):
                if not task.done():
                    task.cancel()
            await asyncio.gather(sender, receiver, return_exceptions=True)
    return collector.result()


def write_result(path, result):
    # Publish a complete output only after successful graceful termination.
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
    # Restrict newly created transcripts and audit logs to their owner.
    os.umask(0o077)
    try:
        key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
        if not key:
            raise SessionError("Set ASSEMBLYAI_API_KEY in the environment.")
        audit_path = Path(str(args.output) + ".frames.jsonl")
        if args.audio.resolve() in {args.output.resolve(), audit_path.resolve()}:
            raise SessionError("Output paths must differ from the audio input.")
        with wave.open(str(args.audio), "rb") as audio:
            if (audio.getnchannels() != 1 or audio.getsampwidth() != 2
                    or audio.getcomptype() != "NONE"
                    or not 8000 <= audio.getframerate() <= 96000):
                raise SessionError("Audio must be mono 16-bit PCM WAV at 8–96 kHz.")
            if audio.getnframes() == 0:
                raise SessionError("Audio file is empty.")
            with audit_path.open("wb") as audit:
                result = asyncio.run(stream(audio, audit, key))
        write_result(args.output, result)
        print(f"Transcription complete: {len(result['turns'])} turns.", file=sys.stderr)
        return 0
    except KeyboardInterrupt:
        print("Transcription interrupted.", file=sys.stderr)
        return 130
    except Exception as exc:
        if isinstance(exc, SessionError):
            print(str(exc), file=sys.stderr)
        else:
            # Avoid logging audio, transcripts, credentials or server error bodies.
            print(f"Transcription failed ({type(exc).__name__}). "
                  "Check input/output paths, connectivity and API access; "
                  "received server messages are in the frames log.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
