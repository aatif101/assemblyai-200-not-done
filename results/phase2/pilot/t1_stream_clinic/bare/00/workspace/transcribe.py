"""Stream a 16 kHz mono WAV to AssemblyAI streaming (v3) at real-time speed."""
import asyncio
import json
import os
import re
import sys
import time
import wave
from urllib.parse import urlencode

import websockets

HOST = "wss://streaming.assemblyai.com/v3/ws"
CHUNK_MS = 50

# Everything identifying; clinical terms (drugs, conditions, procedures) stay readable.
PII_POLICIES = [
    "person_name", "phone_number", "us_social_security_number", "date_of_birth",
    "email_address", "location", "healthcare_number", "account_number",
    "banking_information", "credit_card_number", "credit_card_expiration",
    "credit_card_cvv", "drivers_license", "passport_number", "number_sequence",
    "ip_address", "username", "password", "vehicle_id", "person_age",
    "organization", "url",
]

PARAMS = {
    "sample_rate": 16000,
    "encoding": "pcm_s16le",
    "speech_model": "u3-rt-pro",
    "domain": "medical-v1",
    "speaker_labels": "true",
    "redact_pii": "true",
    "redact_pii_policies": json.dumps(PII_POLICIES),
    "redact_pii_sub": "entity_name",
    # keep a spoken number (SSN, phone) in one turn so redaction sees all of it
    "max_turn_silence": 3000,
}

NUM_WORDS = (
    "zero oh one two three four five six seven eight nine ten eleven twelve thirteen "
    "fourteen fifteen sixteen seventeen eighteen nineteen twenty thirty forty fifty "
    "sixty seventy eighty ninety hundred thousand"
).split()
NUMERIC = r"(?:\d[\d\-.,]*|" + "|".join(NUM_WORDS) + r")"
# a redaction tag for something that is spoken as a run of digits
NUMERIC_TAG = re.compile(
    r"\[(?:US_SOCIAL_SECURITY_NUMBER|PHONE_NUMBER|NUMBER_SEQUENCE|ACCOUNT_NUMBER|"
    r"HEALTHCARE_NUMBER|CREDIT_CARD_\w+|BANKING_INFORMATION|PASSPORT_NUMBER|"
    r"DRIVERS_LICENSE|VEHICLE_ID|DATE_OF_BIRTH)\][\s.,]*$"
)
LEADING_DIGITS = re.compile(r"^\s*(?:" + NUMERIC + r"(?:[\s\-]+|(?=[.,!?]|$)))+[.,!?]?", re.I)
ONLY_DIGITS = re.compile(r"^\s*(?:" + NUMERIC + r"[\s\-.,]*)+$", re.I)


def mask_split_numbers(turns):
    """PII spoken across a turn boundary escapes per-turn redaction (e.g. the tail
    of an SSN); mask a numeric continuation of a masked number, and any
    all-digit turn, ourselves."""
    prev_tag = None
    for t in turns:
        text = t["text"]
        if prev_tag and (m := LEADING_DIGITS.match(text)):
            tag = "[" + prev_tag + "]"
            punct = m.group(0)[-1] if m.group(0)[-1] in ".,!?" else ""
            text = (tag + punct + " " + text[m.end():].lstrip()).strip()
        elif ONLY_DIGITS.match(text):
            text = "[NUMBER_SEQUENCE]"
        t["text"] = text
        tags = NUMERIC_TAG.search(text)
        prev_tag = re.search(r"\[(\w+)\][\s.,]*$", text).group(1) if tags else None
    return turns


def split_by_speaker(msg):
    """A Turn carries one label but its words can come from several speakers."""
    words = msg.get("words") or []
    if not words or any("speaker" not in w for w in words):
        return [{"speaker": msg.get("speaker_label") or "UNKNOWN", "text": msg["transcript"]}]
    out = []
    for w in words:
        if out and out[-1]["speaker"] == w["speaker"]:
            out[-1]["text"] += " " + w["text"]
        else:
            out.append({"speaker": w["speaker"], "text": w["text"]})
    return out


def read_pcm(path):
    with wave.open(path) as w:
        if (w.getframerate(), w.getnchannels(), w.getsampwidth()) != (16000, 1, 2):
            raise SystemExit("audio must be 16 kHz mono 16-bit WAV")
        return w.readframes(w.getnframes())


async def run(audio_path, out_path):
    pcm = read_pcm(audio_path)
    key = os.environ.get("ASSEMBLYAI_API_KEY")
    if not key:
        raise SystemExit("ASSEMBLYAI_API_KEY is not set")

    turns = {}  # turn_order -> Turn message (latest, ended)
    pending = {}  # turn_order -> latest non-ended Turn
    errors = []
    began = terminated = False
    frames_path = out_path + ".frames.jsonl"

    with open(frames_path, "w") as frames:
      async with websockets.connect(
        f"{HOST}?{urlencode(PARAMS)}",
        additional_headers={"Authorization": key},
        max_size=None,
      ) as ws:

        async def receive():
            nonlocal began, terminated
            async for raw in ws:
                frames.write((raw if isinstance(raw, str) else raw.decode()).strip() + "\n")
                frames.flush()
                msg = json.loads(raw)
                t = msg.get("type")
                if t == "Begin":
                    began = True
                elif t == "Turn":
                    o = msg["turn_order"]
                    if msg.get("end_of_turn"):
                        old = turns.get(o)
                        # never let an unformatted repeat replace a formatted one
                        if not (old and old.get("turn_is_formatted") and not msg.get("turn_is_formatted")):
                            turns[o] = msg
                        pending.pop(o, None)
                    else:
                        pending[o] = msg
                elif t == "SpeakerRevision":
                    for r in msg.get("revisions", []):
                        tgt = turns.get(r["turn_order"])
                        if tgt is not None:
                            if r.get("speaker_label") is not None:
                                tgt["speaker_label"] = r["speaker_label"]
                            if r.get("words"):
                                tgt["words"] = r["words"]
                elif t == "Termination":
                    terminated = True
                    return
                elif t == "Error" or "error" in msg:
                    errors.append(msg)

        rx = asyncio.create_task(receive())

        async def send():
            step = 16000 * 2 * CHUNK_MS // 1000
            start = time.monotonic()
            for i, off in enumerate(range(0, len(pcm), step)):
                await ws.send(pcm[off:off + step])
                delay = start + (i + 1) * CHUNK_MS / 1000 - time.monotonic()
                if delay > 0:
                    await asyncio.sleep(delay)
            # trailing silence so the final turn can end
            silence = bytes(step)
            for _ in range(1500 // CHUNK_MS):
                await ws.send(silence)
                await asyncio.sleep(CHUNK_MS / 1000)
            await ws.send(json.dumps({"type": "Terminate"}))

        tx = asyncio.create_task(send())
        done, _ = await asyncio.wait({rx, tx}, return_when=asyncio.FIRST_COMPLETED)
        if tx in done:
            tx.result()
            try:
                await asyncio.wait_for(rx, 30)
            except asyncio.TimeoutError:
                raise SystemExit("timed out waiting for session termination")
        else:
            tx.cancel()
            rx.result()  # surfaces connection errors

    if errors:
        raise SystemExit(f"AssemblyAI error: {errors[0]}")
    if not (began and terminated):
        raise SystemExit("session did not complete cleanly")

    for o, m in pending.items():  # speech cut off before an end-of-turn
        turns.setdefault(o, m)
    out = []
    for _, m in sorted(turns.items()):
        if m["transcript"].strip():
            out.extend(split_by_speaker(m))
    out = mask_split_numbers(out)
    with open(out_path, "w") as f:
        json.dump({"turns": out}, f, indent=2)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: transcribe.py <audio.wav> <out.json>")
    asyncio.run(run(*sys.argv[1:]))
