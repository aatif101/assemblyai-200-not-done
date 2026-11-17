"""Stream a WAV file to AssemblyAI real-time (v3) at real-time speed and
write speaker-labelled, PII-redacted turns plus a raw frame audit log."""
import asyncio, json, os, re, sys, wave
from urllib.parse import urlencode
import websockets

CHUNK_MS = 50
POLICIES = ["person_name", "phone_number", "email_address", "us_social_security_number",
            "date_of_birth", "credit_card_number", "credit_card_expiration", "credit_card_cvv",
            "drivers_license", "banking_information", "account_number", "healthcare_number",
            "passport_number", "password", "username", "ip_address", "vehicle_id",
            "location_address", "location_address_street", "location_city", "location_state",
            "location_zip", "location_coordinate"]


NUMBER_TAGS = {"[US_SOCIAL_SECURITY_NUMBER]", "[PHONE_NUMBER]", "[CREDIT_CARD_NUMBER]",
               "[ACCOUNT_NUMBER]", "[BANKING_INFORMATION]", "[HEALTHCARE_NUMBER]",
               "[CREDIT_CARD_CVV]", "[DRIVERS_LICENSE]", "[PASSPORT_NUMBER]"}
DIGITS = re.compile(r"^[\d\-.,()]*\d[\d\-.,()]*$")


def build_turns(raw_turns):
    """Flatten final turns into words, mask digits trailing a redacted number
    (a turn boundary can split an entity), then regroup by speaker."""
    words = []  # [speaker, text]
    for k in sorted(raw_turns):
        t = raw_turns[k]
        for w in t["words"]:
            words.append([w.get("speaker") or t["speaker"], w["text"]])
        if not t["words"]:
            words.append([t["speaker"], t["text"]])
    out, tag = [], None
    for spk, text in words:
        bare = text.rstrip(".,?!;:")
        if bare in NUMBER_TAGS:
            tag = bare
        elif tag and DIGITS.match(text):
            if out and out[-1][1].rstrip(".,?!;:") == tag:
                tail = text[len(text.rstrip(".,?!;:")):]
                out[-1][1] = tag + tail  # absorb into previous tag
                continue
            text = tag + text[len(text.rstrip(".,?!;:")):]
        else:
            tag = None
        out.append([spk, text])
    turns = []
    for spk, text in out:
        if turns and turns[-1]["speaker"] == spk:
            turns[-1]["text"] += " " + text
        else:
            turns.append({"speaker": spk, "text": text})
    return turns


async def run(wav_path, out_path):
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
        "redact_pii_policies": json.dumps(POLICIES),
        "redact_pii_sub": "entity_name",
        "include_partial_turns": "false",
        "prompt": "Phone intake call at a medical clinic: patient scheduling, symptoms, "
                  "medications, conditions, and procedures.",
    }
    url = "wss://streaming.assemblyai.com/v3/ws?" + urlencode(params)
    turns = {}  # turn_order -> {"speaker", "text"}
    frames_path = out_path + ".frames.jsonl"

    with open(frames_path, "w") as frames:
        async with websockets.connect(url, additional_headers={"Authorization": key},
                                      max_size=None) as ws:
            done = asyncio.Event()

            def log(raw):
                frames.write((raw if isinstance(raw, str) else raw.decode()).strip() + "\n")
                frames.flush()

            async def receive():
                async for raw in ws:
                    log(raw)
                    m = json.loads(raw)
                    t = m.get("type")
                    if t == "Begin":
                        cfg = m.get("configuration") or {}
                        if cfg.get("model") != params["speech_model"]:
                            raise RuntimeError(f"unexpected model: {cfg}")
                        if not cfg.get("redact_pii"):
                            raise RuntimeError("PII redaction not active")
                    elif t == "Turn" and m.get("end_of_turn"):
                        text = (m.get("utterance") or m.get("transcript") or "").strip()
                        if text:
                            turns[m["turn_order"]] = {"speaker": m.get("speaker_label") or "UNKNOWN",
                                                      "text": text, "words": m.get("words") or []}
                    elif t == "SpeakerRevision":
                        for r in m.get("revisions", []):
                            t0 = turns.get(r.get("turn_order"))
                            if t0 and r.get("speaker_label"):
                                t0["speaker"] = r["speaker_label"]
                                if r.get("words") and len(r["words"]) == len(t0["words"]):
                                    for old, new in zip(t0["words"], r["words"]):
                                        old["speaker"] = new.get("speaker") or old.get("speaker")
                    elif t == "Error" or "error" in m:
                        raise RuntimeError(f"server error: {m}")
                    elif t == "Termination":
                        done.set()
                        return
                if not done.is_set():
                    raise RuntimeError("connection closed before Termination")

            async def send():
                step = rate * 2 * CHUNK_MS // 1000
                loop = asyncio.get_running_loop()
                start = loop.time()
                for i, off in enumerate(range(0, len(pcm), step)):
                    await asyncio.sleep(max(0, start + i * CHUNK_MS / 1000 - loop.time()))
                    await ws.send(pcm[off:off + step])
                await ws.send(json.dumps({"type": "Terminate"}))

            rx = asyncio.create_task(receive())
            tx = asyncio.create_task(send())
            await asyncio.wait({rx, tx}, return_when=asyncio.FIRST_EXCEPTION)
            for t in (rx, tx):
                if t.done() and t.exception():
                    for o in (rx, tx):
                        o.cancel()
                    raise t.exception()
            await asyncio.wait_for(rx, timeout=60)

    with open(out_path, "w") as f:
        json.dump({"turns": build_turns(turns)}, f, indent=2)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: run.sh <audio.wav> <out.json>")
    try:
        asyncio.run(run(*sys.argv[1:]))
    except BaseException as e:
        print(f"error: {e!r}", file=sys.stderr)
        sys.exit(1)
