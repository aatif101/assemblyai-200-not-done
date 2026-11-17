"""Stream a 16 kHz mono WAV to AssemblyAI streaming STT at real-time speed.

Medical domain model, speaker labels, and server-side PII redaction.
"""
import asyncio
import contextlib
import json
import os
import re
import sys
import wave
from urllib.parse import urlencode

import websockets

HOST = "wss://streaming.assemblyai.com/v3/ws"
CHUNK_MS = 50
PII_POLICIES = [
    "person_name", "phone_number", "us_social_security_number", "email_address",
    "date_of_birth", "location", "healthcare_number", "account_number",
    "banking_information", "credit_card_number", "credit_card_expiration",
    "credit_card_cvv", "drivers_license", "passport_number", "ip_address",
    "password", "username", "vehicle_id", "person_age", "number_sequence",
]


@contextlib.asynccontextmanager
async def open_async(path):
    with open(path, "w", encoding="utf-8") as f:
        yield f


# Safety net on top of server-side redaction: the server can miss a number that
# is split across turns, so mask long digit runs and SSN/phone shapes ourselves.
_LEAK = re.compile(r"\b\d{3}[-. ]\d{2}[-. ]\d{4}\b|\b\d{3}[-. ]\d{3,4}\b|\b\d{5,}\b")


def scrub(text):
    return _LEAK.sub("[NUMBER]", text)


def segments(turn):
    """Split a turn into consecutive same-speaker runs using per-word labels."""
    words = turn["words"]
    if not words or not any(w.get("speaker") for w in words):
        return [(turn["speaker"], turn["text"])]
    out = []
    for w in words:
        spk = w.get("speaker") or turn["speaker"]
        if out and out[-1][0] == spk:
            out[-1][1].append(w["text"])
        else:
            out.append((spk, [w["text"]]))
    return [(spk, " ".join(ws)) for spk, ws in out]


def read_wav(path):
    with wave.open(path, "rb") as w:
        if (w.getframerate(), w.getnchannels(), w.getsampwidth()) != (16000, 1, 2):
            raise SystemExit("audio must be 16 kHz mono 16-bit PCM WAV")
        return w.readframes(w.getnframes())


async def main(audio_path, out_path):
    key = os.environ.get("ASSEMBLYAI_API_KEY")
    if not key:
        raise SystemExit("ASSEMBLYAI_API_KEY is not set")
    pcm = read_wav(audio_path)
    params = {
        "sample_rate": 16000,
        "encoding": "pcm_s16le",
        "speech_model": "u3-rt-pro",
        "domain": "medical-v1",
        "speaker_labels": "true",
        "format_turns": "true",
        "redact_pii": "true",
        "redact_pii_policies": json.dumps(PII_POLICIES),
        "redact_pii_sub": "entity_name",
        # Keep pauses mid-number (e.g. reading out an SSN) inside one turn so
        # the server can recognise and redact the whole entity.
        "min_turn_silence": 1500,
        "max_turn_silence": 4000,
    }
    turns = {}  # turn_order -> {"speaker", "text", "final"}
    terminated = False
    error = None
    frames_path = out_path + ".frames.jsonl"

    async with websockets.connect(
        f"{HOST}?{urlencode(params)}",
        additional_headers={"Authorization": key},
        max_size=None,
    ) as ws, open_async(frames_path) as frames:

        async def send_audio():
            step = 16000 * 2 * CHUNK_MS // 1000
            loop = asyncio.get_running_loop()
            start = loop.time()
            for n, i in enumerate(range(0, len(pcm), step)):
                await ws.send(pcm[i:i + step])
                delay = start + (n + 1) * CHUNK_MS / 1000 - loop.time()
                if delay > 0:
                    await asyncio.sleep(delay)
            await ws.send(json.dumps({"type": "Terminate"}))

        sender = asyncio.create_task(send_audio())
        try:
            async for raw in ws:
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", "replace")
                frames.write(raw)
                frames.write("\n")
                frames.flush()
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                t = msg.get("type")
                if t == "Turn":
                    order = msg["turn_order"]
                    cur = turns.get(order)
                    if cur and cur["final"] and not msg.get("end_of_turn"):
                        continue  # stale partial after finalization
                    # With format_turns, the formatted message supersedes the
                    # unformatted end-of-turn one.
                    if cur and cur["formatted"] and not msg.get("turn_is_formatted") \
                            and msg.get("end_of_turn"):
                        continue
                    turns[order] = {
                        "speaker": msg.get("speaker_label") or (cur or {}).get("speaker"),
                        "text": msg.get("transcript", ""),
                        "words": msg.get("words") or [],
                        "final": bool(msg.get("end_of_turn")),
                        "formatted": bool(msg.get("turn_is_formatted")),
                    }
                elif t == "SpeakerRevision":
                    for r in msg.get("revisions", []):
                        cur = turns.get(r.get("turn_order"))
                        if not cur:
                            continue
                        if r.get("speaker_label"):
                            cur["speaker"] = r["speaker_label"]
                        by_start = {w.get("start"): w.get("speaker") for w in r.get("words", [])}
                        for w in cur["words"]:
                            if by_start.get(w.get("start")):
                                w["speaker"] = by_start[w["start"]]
                elif t == "Error":
                    error = msg.get("error") or "unknown error"
                elif t == "Termination":
                    terminated = True
                    break
        finally:
            sender.cancel()

    if error:
        raise SystemExit(f"AssemblyAI error: {error}")
    if not terminated:
        raise SystemExit("connection closed before Termination")
    out = [
        {"speaker": spk or "UNKNOWN", "text": scrub(text)}
        for k in sorted(turns)
        for spk, text in segments(turns[k])
        if text.strip()
    ]
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"turns": out}, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: run.sh <audio.wav> <out.json>")
    try:
        asyncio.run(main(*sys.argv[1:]))
    except SystemExit:
        raise
    except Exception as e:
        raise SystemExit(f"failed: {type(e).__name__}: {e}")
