"""Stream a 16 kHz mono WAV to AssemblyAI real-time STT at real-time speed.

Medical mode + speaker labels + PII redaction. Writes the final turns to
<out.json> and every received server message, verbatim, to <out.json>.frames.jsonl.
"""
import asyncio
import json
import os
import sys
import time
import wave
from urllib.parse import quote

import websockets

URL = "wss://streaming.assemblyai.com/v3/ws"
CHUNK_MS = 50

# Identifiers only. Deliberately NOT drug / medical_condition / medical_process /
# injury / statistics / blood_type / duration: redacting those would destroy the
# medical content we want transcribed accurately.
PII_POLICIES = [
    "person_name", "person_age", "phone_number", "email_address",
    "us_social_security_number", "date_of_birth", "credit_card_number",
    "credit_card_cvv", "credit_card_expiration", "banking_information",
    "account_number", "healthcare_number", "drivers_license", "passport_number",
    "password", "username", "ip_address", "url", "vehicle_id", "number_sequence",
    "location", "location_address", "location_address_street", "location_city",
    "location_state", "location_zip", "location_coordinate",
]

KEYTERMS = [
    "Lisinopril", "Metformin", "Atorvastatin", "Humalog", "hypertension",
    "diabetes mellitus", "hemoglobin A1c", "echocardiogram", "colonoscopy",
]


async def run(wav_path, out_path):
    key = os.environ.get("ASSEMBLYAI_API_KEY")
    if not key:
        raise RuntimeError("ASSEMBLYAI_API_KEY is not set")

    with wave.open(wav_path, "rb") as w:
        if (w.getframerate(), w.getnchannels(), w.getsampwidth()) != (16000, 1, 2):
            raise RuntimeError("audio must be 16 kHz mono 16-bit WAV")
        pcm = w.readframes(w.getnframes())

    params = {
        "sample_rate": 16000,
        "encoding": "pcm_s16le",
        "speech_model": "universal-3-6-pro",
        "domain": "medical-v1",
        "keyterms_prompt": json.dumps(KEYTERMS),
        "speaker_labels": "true",
        "redact_pii": "true",
        "redact_pii_policies": json.dumps(PII_POLICIES),
        "redact_pii_sub": "entity_name",
        # Partial turns would carry unredacted PII.
        "include_partial_turns": "false",
    }
    query = "&".join(f"{k}={quote(str(v))}" for k, v in params.items())

    turns = {}  # turn_order -> final Turn message
    state = {"begin": False, "terminated": False}

    with open(out_path + ".frames.jsonl", "w") as frames:
        async with websockets.connect(
            f"{URL}?{query}", additional_headers={"Authorization": key}, max_size=None
        ) as ws:

            async def receiver():
                async for raw in ws:
                    frames.write((raw if isinstance(raw, str) else raw.decode()).rstrip("\n") + "\n")
                    frames.flush()
                    msg = json.loads(raw)
                    t = msg.get("type")
                    if t == "Begin":
                        state["begin"] = True
                        model = msg.get("configuration", {}).get("model")
                        if model and model != params["speech_model"]:
                            print(f"warning: server model is {model}", file=sys.stderr)
                    elif t == "Turn":
                        if msg.get("end_of_turn") and msg.get("turn_is_formatted", True):
                            turns[msg["turn_order"]] = msg
                    elif t == "SpeakerRevision":
                        for rev in msg.get("revisions", []):
                            turn = turns.get(rev["turn_order"])
                            if turn is not None and rev.get("speaker_label") is not None:
                                turn["speaker_label"] = rev["speaker_label"]
                    elif t == "Termination":
                        state["terminated"] = True
                    elif t == "Error":
                        raise RuntimeError(f"AssemblyAI error: {msg}")

            recv = asyncio.create_task(receiver())

            async def sender():
                step = 16000 * 2 * CHUNK_MS // 1000
                start = time.monotonic()
                for i, off in enumerate(range(0, len(pcm), step)):
                    await ws.send(pcm[off:off + step])
                    delay = start + (i + 1) * CHUNK_MS / 1000 - time.monotonic()
                    if delay > 0:
                        await asyncio.sleep(delay)
                await ws.send(json.dumps({"type": "Terminate"}))

            send = asyncio.create_task(sender())
            done, _ = await asyncio.wait({recv, send}, return_when=asyncio.FIRST_EXCEPTION)
            for t in done:
                t.result()
            await asyncio.wait_for(recv, timeout=120)

    if not state["begin"] or not state["terminated"]:
        raise RuntimeError("session did not complete cleanly")

    out = {"turns": [
        {"speaker": turns[k].get("speaker_label", "UNKNOWN"), "text": turns[k]["transcript"]}
        for k in sorted(turns) if turns[k].get("transcript", "").strip()
    ]}
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2)


def main():
    if len(sys.argv) != 3:
        print("usage: run.sh <audio.wav> <out.json>", file=sys.stderr)
        return 2
    try:
        asyncio.run(run(*sys.argv[1:]))
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


sys.exit(main())
