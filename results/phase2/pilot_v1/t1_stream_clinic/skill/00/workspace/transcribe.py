import asyncio, json, os, sys, wave, urllib.parse
import websockets

URL = "wss://streaming.us.assemblyai.com/v3/ws"
CHUNK_MS = 50


def params():
    return {
        "speech_model": "universal-3-6-pro",
        "sample_rate": 16000,
        "encoding": "pcm_s16le",
        "domain": "medical-v1",
        "prompt": "Phone intake call at a medical clinic between a staff member and a patient, discussing symptoms, conditions, medications, dosages, and appointments.",
        "speaker_labels": "true",
        "redact_pii": "true",
        "redact_pii_policies": json.dumps([
            "person_name", "phone_number", "email_address", "us_social_security_number",
            "date_of_birth", "location", "location_address", "credit_card_number",
            "banking_information", "drivers_license", "passport_number", 
            "healthcare_number", "person_age", "credit_card_expiration", "credit_card_cvv", "location_address_street", "location_city", "location_zip", "location_coordinate", "number_sequence", "account_number", "ip_address", "username", "password",
        ]),
        "redact_pii_sub": "entity_name",
        "include_partial_turns": "false",
    }


async def run(wav_path, out_path):
    key = os.environ["ASSEMBLYAI_API_KEY"]
    with wave.open(wav_path, "rb") as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1 and w.getsampwidth() == 2, "need 16 kHz mono 16-bit WAV"
        pcm = w.readframes(w.getnframes())
    step = 16000 * 2 * CHUNK_MS // 1000
    turns = {}  # turn_order -> {speaker, text}
    began = False
    terminated = False

    with open(out_path + ".frames.jsonl", "w") as frames:
        async with websockets.connect(URL + "?" + urllib.parse.urlencode(params()),
                                      additional_headers={"Authorization": key},
                                      max_size=None) as ws:

            async def sender():
                loop = asyncio.get_running_loop()
                t0 = loop.time()
                for i, off in enumerate(range(0, len(pcm), step)):
                    await asyncio.sleep(max(0, t0 + i * CHUNK_MS / 1000 - loop.time()))
                    await ws.send(pcm[off:off + step])
                await ws.send(json.dumps({"type": "Terminate"}))

            send_task = asyncio.create_task(sender())
            try:
                async for raw in ws:
                    frames.write((raw if isinstance(raw, str) else raw.decode()).rstrip("\n") + "\n")
                    frames.flush()
                    msg = json.loads(raw)
                    t = msg.get("type")
                    if t == "Begin":
                        began = True
                        cfg = msg.get("configuration") or {}
                        if cfg.get("redact_pii") is False or cfg.get("speaker_labels") is False:
                            raise RuntimeError(f"server did not apply requested features: {cfg}")
                    elif t == "Turn":
                        if msg.get("end_of_turn") and msg.get("transcript", "").strip():
                            turns[msg["turn_order"]] = {
                                "speaker": msg.get("speaker_label") or "UNKNOWN",
                                "text": msg.get("utterance") or msg["transcript"],
                            }
                    elif t == "SpeakerRevision":
                        for r in msg.get("revisions", []):
                            if r.get("turn_order") in turns and r.get("speaker_label"):
                                turns[r["turn_order"]]["speaker"] = r["speaker_label"]
                    elif t == "Termination":
                        terminated = True
                        break
                    elif t == "Error" or "error" in msg:
                        raise RuntimeError(f"server error: {msg}")
            finally:
                send_task.cancel()
    if not began or not terminated:
        raise RuntimeError("session ended without Begin/Termination")
    with open(out_path, "w") as f:
        json.dump({"turns": [turns[k] for k in sorted(turns)]}, f, indent=2)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: run.sh <audio.wav> <out.json>")
    try:
        asyncio.run(run(*sys.argv[1:]))
    except Exception as e:
        print(f"error: {e!r}", file=sys.stderr)
        sys.exit(1)
