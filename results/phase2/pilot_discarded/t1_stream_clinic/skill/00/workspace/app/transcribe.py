"""Stream a WAV file to AssemblyAI real-time STT at real-time speed.

Medical Mode, speaker labels and PII redaction are enabled. Every received
frame is written verbatim to <out>.frames.jsonl; final turns go to <out>.
"""
import asyncio
import json
import os
import sys
import wave
from urllib.parse import urlencode

import websockets

CHUNK_MS = 50
PII_POLICIES = [
    "person_name", "phone_number", "us_social_security_number", "email_address",
    "date_of_birth", "credit_card_number", "credit_card_cvv", "credit_card_expiration",
    "location_address", "location_address_street", "location_city", "location_zip",
    "location_coordinate", "healthcare_number", "account_number", "banking_information",
    "drivers_license", "passport_number", "ip_address", "username", "password",
    "vehicle_id",
]  # deliberately excludes drug / medical_condition / medical_process


async def main(wav_path, out_path):
    key = os.environ["ASSEMBLYAI_API_KEY"]
    with wave.open(wav_path, "rb") as w:
        rate, ch, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        if ch != 1 or width != 2:
            raise SystemExit("expected 16-bit mono WAV")
        pcm = w.readframes(w.getnframes())

    params = {
        "speech_model": "universal-3-6-pro",
        "sample_rate": rate,
        "encoding": "pcm_s16le",
        "domain": "medical-v1",
        "speaker_labels": "true",
        "redact_pii": "true",
        "redact_pii_policies": json.dumps(PII_POLICIES),
        "redact_pii_sub": "entity_name",
    }
    url = "wss://streaming.assemblyai.com/v3/ws?" + urlencode(params)

    turns = {}  # turn_order -> {"speaker", "text"}
    began = False
    terminated = asyncio.Event()

    with open(out_path + ".frames.jsonl", "w") as frames:
        async with websockets.connect(
            url, additional_headers={"Authorization": key}, max_size=None
        ) as ws:

            async def receive():
                nonlocal began
                async for raw in ws:
                    frames.write((raw if isinstance(raw, str) else raw.decode()).strip() + "\n")
                    frames.flush()
                    msg = json.loads(raw)
                    t = msg.get("type")
                    if t == "Begin":
                        cfg = msg.get("configuration") or {}
                        if cfg.get("speaker_labels") is not True or cfg.get("redact_pii") is not True \
                                or cfg.get("domain") != "medical-v1" \
                                or cfg.get("model") != "universal-3-6-pro":
                            raise RuntimeError(f"server did not apply requested config: {cfg}")
                        began = True
                    elif t == "Turn" and msg.get("end_of_turn"):
                        turns[msg["turn_order"]] = {
                            "speaker": msg.get("speaker_label") or "UNKNOWN",
                            "text": msg.get("transcript", ""),
                        }
                    elif t == "SpeakerRevision":
                        for r in msg.get("revisions", []):
                            if r["turn_order"] in turns and r.get("speaker_label"):
                                turns[r["turn_order"]]["speaker"] = r["speaker_label"]
                    elif t == "Error" or "error" in msg:
                        raise RuntimeError(f"server error: {msg}")
                    elif t == "Termination":
                        terminated.set()
                        return

            async def send():
                step = rate * 2 * CHUNK_MS // 1000
                loop = asyncio.get_running_loop()
                start = loop.time()
                for i, off in enumerate(range(0, len(pcm), step)):
                    await ws.send(pcm[off:off + step])
                    await asyncio.sleep(max(0, start + (i + 1) * CHUNK_MS / 1000 - loop.time()))
                await ws.send(json.dumps({"type": "Terminate"}))

            rx = asyncio.create_task(receive())
            tx = asyncio.create_task(send())
            done, pending = await asyncio.wait({rx, tx}, return_when=asyncio.FIRST_EXCEPTION)
            for d in done:
                d.result()
            await asyncio.wait_for(rx, timeout=60)
            tx.result()

    if not terminated.is_set() or not began:
        raise RuntimeError("session did not complete cleanly")

    result = {"turns": [turns[k] for k in sorted(turns) if turns[k]["text"].strip()]}
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: run.sh <audio.wav> <out.json>")
    try:
        asyncio.run(main(sys.argv[1], sys.argv[2]))
    except BaseException as e:
        if isinstance(e, SystemExit):
            raise
        print(f"error: {e!r}", file=sys.stderr)
        sys.exit(1)
