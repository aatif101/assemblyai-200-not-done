import asyncio, json, os, sys, wave
from urllib.parse import urlencode
import websockets

URL = "wss://streaming.us.assemblyai.com/v3/ws"
CHUNK_MS = 50
# Identifiers only: the default (all) would also mask drugs/conditions/procedures.
PII = json.dumps([
    "person_name", "phone_number", "email_address", "us_social_security_number",
    "date_of_birth", "credit_card_number", "credit_card_cvv", "credit_card_expiration",
    "banking_information", "account_number", "healthcare_number", "drivers_license",
    "passport_number", "location_address", "location_address_street", "location_zip",
    "location_coordinate", "ip_address", "username", "password", "vehicle_id",
])
PROMPT = ("Phone intake call at a medical clinic between a staff member and a patient, "
          "covering symptoms, conditions, medications with dosages, and procedures.")


async def main(wav_path, out_path):
    key = os.environ["ASSEMBLYAI_API_KEY"]
    with wave.open(wav_path, "rb") as w:
        if w.getsampwidth() != 2 or w.getnchannels() != 1:
            raise SystemExit("need 16-bit mono WAV")
        rate = w.getframerate()
        pcm = w.readframes(w.getnframes())
    params = dict(speech_model="universal-3-6-pro", sample_rate=rate, encoding="pcm_s16le",
                  domain="medical-v1", speaker_labels="true", redact_pii="true",
                  redact_pii_policies=PII, redact_pii_sub="entity_name", prompt=PROMPT)
    turns = {}  # turn_order -> {speaker, text}
    done = asyncio.Event()
    frames = open(out_path + ".frames.jsonl", "w")

    async with websockets.connect(f"{URL}?{urlencode(params)}",
                                  additional_headers={"Authorization": key},
                                  max_size=None) as ws:
        async def receive():
            async for raw in ws:
                frames.write((raw if isinstance(raw, str) else raw.decode()).rstrip("\n") + "\n")
                frames.flush()
                m = json.loads(raw)
                t = m.get("type")
                if t == "Begin":
                    c = m.get("configuration") or {}
                    if c.get("model") != params["speech_model"] or not c.get("redact_pii"):
                        raise RuntimeError(f"server config mismatch: {c}")
                elif t == "Turn" and m.get("end_of_turn"):
                    turns[m["turn_order"]] = {"speaker": m.get("speaker_label", ""),
                                              "text": m.get("utterance") or m.get("transcript", "")}
                elif t == "SpeakerRevision":
                    for r in m.get("revisions", []):
                        if r.get("turn_order") in turns:
                            turns[r["turn_order"]]["speaker"] = r.get("speaker_label", turns[r["turn_order"]]["speaker"])
                elif t == "Error" or "error" in m:
                    raise RuntimeError(f"AssemblyAI error: {m}")
                elif t == "Termination":
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
        try:
            await asyncio.wait_for(asyncio.gather(rx, tx), timeout=len(pcm) / (rate * 2) + 120)
        finally:
            for t in (rx, tx):
                t.cancel()
            frames.close()
    out = {"turns": [turns[k] for k in sorted(turns)]}
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: run.sh <audio.wav> <out.json>")
    try:
        asyncio.run(main(*sys.argv[1:]))
    except BaseException as e:
        print(f"error: {e!r}", file=sys.stderr)
        sys.exit(1)
