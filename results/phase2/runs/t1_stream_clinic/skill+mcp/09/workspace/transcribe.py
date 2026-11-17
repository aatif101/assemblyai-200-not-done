import asyncio, json, os, sys, wave
from urllib.parse import urlencode
import websockets

URL = "wss://streaming.us.assemblyai.com/v3/ws"
CHUNK_MS = 50
TAIL_SILENCE_MS = 2000  # lets the final turn endpoint, like a line going quiet


def build_url(rate):
    q = {
        "speech_model": "universal-3-6-pro",
        "sample_rate": rate,
        "encoding": "pcm_s16le",
        "domain": "medical-v1",
        "speaker_labels": "true",
        "max_speakers": 4,
        "redact_pii": "true",
        "redact_pii_sub": "entity_name",
        # explicit broad policy list so nothing identifying slips through
        "redact_pii_policies": json.dumps([
            "person_name", "phone_number", "email_address", "date_of_birth",
            "us_social_security_number", "credit_card_number",
            "credit_card_expiration", "credit_card_cvv", "location",
            "healthcare_number", "drivers_license", "passport_number",
            "banking_information", "account_number", "ip_address", "username",
            "password", "person_age", "number_sequence", "vehicle_id",
        ]),
    }
    return URL + "?" + urlencode(q)


async def run(wav_path, out_path):
    with wave.open(wav_path, "rb") as w:
        if w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise SystemExit("expected 16-bit mono WAV")
        rate = w.getframerate()
        pcm = w.readframes(w.getnframes())
    pcm += b"\0" * (rate * 2 * TAIL_SILENCE_MS // 1000)
    step = rate * 2 * CHUNK_MS // 1000

    turns = {}  # turn_order -> {"speaker", "text"}
    frames = open(out_path + ".frames.jsonl", "w", encoding="utf-8")
    headers = {"Authorization": os.environ["ASSEMBLYAI_API_KEY"]}

    async with websockets.connect(build_url(rate), additional_headers=headers,
                                  max_size=None) as ws:
        async def sender():
            loop = asyncio.get_running_loop()
            t0 = loop.time()
            for i, off in enumerate(range(0, len(pcm), step)):
                delay = t0 + i * CHUNK_MS / 1000 - loop.time()
                if delay > 0:
                    await asyncio.sleep(delay)
                await ws.send(pcm[off:off + step])
            await ws.send(json.dumps({"type": "Terminate"}))

        send_task = None
        try:
            async for raw in ws:
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8")
                frames.write(raw.replace("\n", " ") + "\n")
                frames.flush()
                msg = json.loads(raw)
                t = msg.get("type")
                if t == "Begin":
                    cfg = msg.get("configuration") or {}
                    if cfg.get("model") != "universal-3-6-pro" or not cfg.get("redact_pii") \
                            or not cfg.get("speaker_labels") or cfg.get("domain") != "medical-v1":
                        raise RuntimeError(f"server did not apply requested config: {cfg}")
                    send_task = asyncio.create_task(sender())
                elif t == "Turn" and msg.get("end_of_turn"):
                    text = (msg.get("utterance") or msg.get("transcript") or "").strip()
                    if text:
                        turns[msg["turn_order"]] = {
                            "speaker": msg.get("speaker_label") or "UNKNOWN", "text": text}
                elif t == "SpeakerRevision":
                    for r in msg.get("revisions", []):
                        if r.get("turn_order") in turns and r.get("speaker_label"):
                            turns[r["turn_order"]]["speaker"] = r["speaker_label"]
                elif t == "Error" or "error" in msg:
                    raise RuntimeError(f"server error: {raw}")
                elif t == "Termination":
                    break
            else:
                raise RuntimeError("connection closed before Termination")
            if send_task:
                await send_task
        finally:
            if send_task and not send_task.done():
                send_task.cancel()
            frames.close()

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"turns": [turns[k] for k in sorted(turns)]}, f, indent=2)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: run.sh <audio.wav> <out.json>")
    try:
        asyncio.run(run(sys.argv[1], sys.argv[2]))
    except SystemExit:
        raise
    except Exception as e:
        print(f"error: {e!r}", file=sys.stderr)
        sys.exit(1)
