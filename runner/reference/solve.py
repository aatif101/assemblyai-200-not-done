"""Reference solutions for the four Phase 2 tasks, in a right and a wrong variant.

Used only by `make oracle-check` to show each oracle passes a correct integration and fails a
known-wrong one, against the live API. Never copied into an agent's workspace.

    python runner/reference/solve.py <task> <good|bad> <audio.wav> <out.json>

The wrong variants are the quiet paths Phase 1 measured:
  t1  speakerLabels=true (camelCase, ignored by the server)      stream.param.camel_case
  t2  redact_pii_policies narrowed to SSNs only                  (names and phone numbers kept)
  t3  no localization                                            (American spelling by default)
  t4  word_boost + boost_param kept after the upgrade            async.word_boost
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.parse
import wave

import requests

API = "https://api.assemblyai.com"
KEY = os.environ["ASSEMBLYAI_API_KEY"]
H = {"Authorization": KEY}


def upload(path: str) -> str:
    with open(path, "rb") as f:
        r = requests.post(f"{API}/v2/upload", headers=H, data=f, timeout=120)
    r.raise_for_status()
    return r.json()["upload_url"]


def transcribe(path: str, opts: dict) -> dict:
    r = requests.post(f"{API}/v2/transcript", headers=H, json={"audio_url": upload(path), **opts}, timeout=60)
    r.raise_for_status()
    tid = r.json()["id"]
    for _ in range(150):
        t = requests.get(f"{API}/v2/transcript/{tid}", headers=H, timeout=60).json()
        if t["status"] in ("completed", "error"):
            break
        time.sleep(2)
    if t["status"] != "completed":
        sys.exit(f"transcription failed: {t.get('error')}")
    return t


def async_out(t: dict, **extra) -> dict:
    return {"transcript_id": t["id"], "text": t.get("text") or "",
            "utterances": [{"speaker": u["speaker"], "text": u["text"]} for u in t.get("utterances") or []], **extra}


def t1(variant: str, audio: str, out: str) -> None:
    from websockets.sync.client import connect

    q = {"speech_model": "universal-3-6-pro", "sample_rate": 16000, "encoding": "pcm_s16le",
         "domain": "medical-v1", "redact_pii": "true"}
    q["speaker_labels" if variant == "good" else "speakerLabels"] = "true"
    url = "wss://streaming.assemblyai.com/v3/ws?" + urllib.parse.urlencode(q)
    with wave.open(audio, "rb") as w:
        pcm = w.readframes(w.getnframes())
    step = 16000 * 2 // 20  # 50 ms
    turns: list[dict] = []
    with open(out + ".frames.jsonl", "w", encoding="utf-8") as log, connect(url, additional_headers=H) as ws:
        def drain(timeout: float) -> bool:
            end = time.monotonic() + timeout
            while True:
                left = end - time.monotonic()
                if left <= 0:
                    return False
                try:
                    raw = ws.recv(timeout=left)
                except TimeoutError:
                    return False
                log.write(raw if isinstance(raw, str) else json.dumps({"_binary_len": len(raw)}))
                log.write("\n")
                msg = json.loads(raw) if isinstance(raw, str) else {}
                if msg.get("type") == "Turn" and msg.get("end_of_turn"):
                    turns.append({"speaker": msg.get("speaker_label"), "text": msg.get("transcript", "")})
                if msg.get("type") == "Termination":
                    return True

        for i in range(0, len(pcm), step):
            ws.send(pcm[i:i + step])
            drain(0.05)  # also paces the stream at real time
        ws.send(json.dumps({"type": "Terminate"}))
        drain(30)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"turns": turns}, f, indent=2)


def t2(variant: str, audio: str, out: str) -> None:
    pol = ["person_name", "phone_number", "us_social_security_number"] if variant == "good" else ["us_social_security_number"]
    t = transcribe(audio, {"speaker_labels": True, "redact_pii": True, "redact_pii_policies": pol})
    with open(out, "w", encoding="utf-8") as f:
        json.dump(async_out(t), f, indent=2)


def t3(variant: str, audio: str, out: str) -> None:
    opts = {"language_detection": True, "language_detection_options": {"localization": ["en_uk"]}} if variant == "good" else {}
    t = transcribe(audio, opts)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"transcript_id": t["id"], "text": t.get("text") or ""}, f, indent=2)


def t4(variant: str, audio: str, out: str) -> None:
    names = ["Quorvex", "Zyntrix Flow", "Talvanta"]
    opts: dict = {"speaker_labels": True,
                  "speech_understanding": {"request": {"summarization": {"summary_type": "paragraph"}}}}
    if variant == "good":
        opts["keyterms_prompt"] = names
    else:
        opts.update(word_boost=names, boost_param="high")
    t = transcribe(audio, opts)
    summ = ((t.get("speech_understanding") or {}).get("response") or {}).get("summarization") or {}
    with open(out, "w", encoding="utf-8") as f:
        json.dump(async_out(t, summary=summ.get("block_summary") or ""), f, indent=2)


if __name__ == "__main__":
    task, variant, audio, out = sys.argv[1:5]
    {"t1_stream_clinic": t1, "t2_async_redact": t2, "t3_british": t3, "t4_legacy_upgrade": t4}[task](variant, audio, out)
