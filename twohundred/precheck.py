"""Phase 2 live pre-checks for T3 (Q3) and T4 (Q4).

Collection is live (`python -m twohundred precheck run`), decision is offline and pure
(`python -m twohundred precheck decide`), the same split as Phase 1.

The rules below were written on 2026-11-15, before any pre-check request was sent.

Q3. Is "British spelling" gradable from the server?
    Clip: verbatim_memo.wav (12.1 s; ground truth: 4 words with British/American forms), and
    the same clip played twice with 0.5 s of silence between (24.7 s), because the skill says
    language detection needs at least 15 s of speech.
    Requests per clip:
      baseline    {}
      localized   {"language_detection": true, "language_detection_options": {"localization": ["en_uk"]}}
                  (the skill's documented path: SKILL.md "English localization", api-reference.md section 12)
      code_en_uk  {"language_code": "en_uk"}  (an older shape an agent may reach for; informational)
    A clip length PASSES when
      - baseline has at most 1 of the 4 British forms (the default does not already give British), and
      - localized completed, has at least 3 of the 4 British forms, at most 1 American form,
        and language_code == "en_uk".
    Verdict: gradable if the 12 s clip passes (T3 uses verbatim_memo.wav), else gradable on the
    long clip if that passes (T3 uses the doubled clip, committed as a fixture), else not
    gradable and T3 becomes the key-terms task on product_memo.wav (decision 20).
    code_en_uk never decides the verdict. It is recorded so the T3 oracle knows whether a second
    request shape also produces British spelling.

Q4. Does Speech Understanding summarization work on this account?
    Clip: product_memo.wav.
    Request: {"speech_understanding": {"request": {"summarization": {"summary_type": "paragraph"}}}}
    PASSES when the transcript completed, speech_understanding.response.summarization.status is
    "success" and block_summary is a non-empty string.
    Verdict: pass means the T4 seed repo includes a LeMUR summary (decision 21); fail means it does not.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

import requests

from .audio import read_wav, silence, wav_bytes
from .config import AUDIO, ENDPOINTS, RESULTS, SCRIPTS, Settings
from .transport import Req, http_call

OUT = RESULTS / "phase2" / "precheck"
RAW = OUT / "raw"

VERBATIM = "verbatim_memo.wav"
PRODUCT = "product_memo.wav"
LONG = "verbatim_memo_x2"  # built in memory from VERBATIM; never a file unless T3 needs it

Q3_REQUESTS: dict[str, dict[str, Any]] = {
    "baseline": {},
    "localized": {"language_detection": True, "language_detection_options": {"localization": ["en_uk"]}},
    "code_en_uk": {"language_code": "en_uk"},
}
Q4_REQUEST = {"speech_understanding": {"request": {"summarization": {"summary_type": "paragraph"}}}}


def _ground_truth() -> dict[str, Any]:
    return json.loads((SCRIPTS / "verbatim_memo.json").read_text(encoding="utf-8"))["ground_truth"]


def doubled_clip() -> bytes:
    """verbatim_memo.wav twice, 0.5 s apart. Deterministic, so it can be rebuilt as a fixture."""
    pcm, rate, channels = read_wav(AUDIO / VERBATIM)
    return wav_bytes(pcm + silence(0.5, rate) + pcm, rate, channels)


# ---------------------------------------------------------------- decision (pure, offline)


def count_forms(text: str | None, words: list[str]) -> list[str]:
    low = (text or "").lower()
    return [w for w in words if re.search(rf"\b{re.escape(w.lower())}\b", low)]


def _final(rec: dict | None) -> dict:
    return (rec or {}).get("final") or {}


def spelling(rec: dict | None) -> dict[str, Any]:
    gt = _ground_truth()
    t = _final(rec)
    return {
        "status": t.get("status") or (f"HTTP {(rec or {}).get('response', {}).get('status')}" if rec else "not run"),
        "language_code": t.get("language_code"),
        "speech_model_used": t.get("speech_model_used"),
        "british": count_forms(t.get("text"), gt["british_spellings"]),
        "american": count_forms(t.get("text"), gt["american_spellings"]),
        "error": t.get("error") or ((rec or {}).get("response") or {}).get("json", {}).get("error"),
    }


def decide_q3(records: dict[str, dict[str, dict]]) -> dict[str, Any]:
    """records[clip][request_name] -> raw record. Clips: VERBATIM, LONG."""
    per_clip: dict[str, Any] = {}
    for clip in (VERBATIM, LONG):
        recs = records.get(clip, {})
        rows = {name: spelling(recs.get(name)) for name in Q3_REQUESTS}
        base, loc = rows["baseline"], rows["localized"]
        base_ok = base["status"] == "completed" and len(base["british"]) <= 1
        loc_ok = (loc["status"] == "completed" and len(loc["british"]) >= 3 and len(loc["american"]) <= 1
                  and loc["language_code"] == "en_uk")
        code = rows["code_en_uk"]
        per_clip[clip] = {"requests": rows, "baseline_ok": base_ok, "localized_ok": loc_ok,
                          "passes": base_ok and loc_ok,
                          "code_en_uk_gives_british": code["status"] == "completed" and len(code["british"]) >= 3}
    if per_clip[VERBATIM]["passes"]:
        verdict, fixture = "gradable", VERBATIM
    elif per_clip[LONG]["passes"]:
        verdict, fixture = "gradable_long_clip", f"{LONG}.wav"
    else:
        verdict, fixture = "not_gradable", PRODUCT
    t3 = "british_spelling" if verdict.startswith("gradable") else "key_terms"
    return {"verdict": verdict, "t3": t3, "t3_fixture": fixture, "clips": per_clip}


def decide_q4(rec: dict | None) -> dict[str, Any]:
    t = _final(rec)
    summ = ((t.get("speech_understanding") or {}).get("response") or {}).get("summarization") or {}
    block = summ.get("block_summary")
    ok = t.get("status") == "completed" and summ.get("status") == "success" and isinstance(block, str) and bool(block.strip())
    http_status = ((rec or {}).get("response") or {}).get("status")
    return {
        "verdict": "works" if ok else "does_not_work",
        "t4_includes_lemur": ok,
        "transcript_status": t.get("status"),
        "http_status": http_status,
        "summarization_status": summ.get("status"),
        "block_summary_chars": len(block) if isinstance(block, str) else 0,
        "error": t.get("error") or ((rec or {}).get("response") or {}).get("json", {}).get("error"),
    }


def load_records() -> tuple[dict[str, dict[str, dict]], dict | None]:
    q3: dict[str, dict[str, dict]] = {}
    q4 = None
    for path in sorted(RAW.glob("*.json")):
        rec = json.loads(path.read_text(encoding="utf-8"))
        if rec.get("check") == "q3":
            q3.setdefault(rec["clip"], {})[rec["request_name"]] = rec
        elif rec.get("check") == "q4":
            q4 = rec
    return q3, q4


def report(q3: dict[str, Any], q4: dict[str, Any]) -> str:
    lines = ["# Phase 2 pre-checks (Q3, Q4)", "",
             "Generated by `python -m twohundred precheck decide` from `results/phase2/precheck/raw/`. "
             "Rules: `twohundred/precheck.py` docstring, written before the requests were sent.", "",
             f"## Q3: British spelling - **{q3['verdict']}** -> T3 is `{q3['t3']}` on `{q3['t3_fixture']}`", "",
             "| Clip | Request | Status | language_code | British forms | American forms | Model |",
             "|---|---|---|---|---|---|---|"]
    for clip, c in q3["clips"].items():
        for name, r in c["requests"].items():
            lines.append(f"| {clip} | {name} | {r['status']} | {r['language_code']} | "
                         f"{len(r['british'])}/4 {r['british']} | {len(r['american'])}/4 {r['american']} | "
                         f"{r['speech_model_used']} |")
        lines.append(f"| {clip} | **passes** | {c['passes']} (baseline ok: {c['baseline_ok']}, "
                     f"localized ok: {c['localized_ok']}) | | | | |")
    lines += ["", f"## Q4: Speech Understanding summarization - **{q4['verdict']}** -> "
              f"T4 seed {'includes' if q4['t4_includes_lemur'] else 'does not include'} a LeMUR summary", "",
              f"- HTTP status: {q4['http_status']}; transcript status: {q4['transcript_status']}",
              f"- summarization status: {q4['summarization_status']}; block_summary length: {q4['block_summary_chars']} chars",
              f"- error: {q4['error']}", ""]
    return "\n".join(lines)


# ---------------------------------------------------------------- collection (live)


def _upload(settings: Settings, data: bytes, label: str) -> str:
    r = requests.post(f"{ENDPOINTS['async']}/v2/upload",
                      headers={"Authorization": settings.api_key, "Content-Type": "application/octet-stream"},
                      data=data, timeout=120)
    if r.status_code != 200:
        sys.exit(f"Upload of {label} failed with HTTP {r.status_code}: {settings.scrubber.scrub(r.text[:300])}")
    return r.json()["upload_url"]


def run(settings: Settings, write_json, now, force: bool = False) -> None:
    jobs: list[tuple[str, str, str, dict]] = []  # (file stem, check, clip, body)
    for clip in (VERBATIM, LONG):
        for name, opts in Q3_REQUESTS.items():
            jobs.append((f"q3.{clip.removesuffix('.wav')}.{name}", "q3", clip, {"request_name": name, "opts": opts}))
    jobs.append(("q4.summarization", "q4", PRODUCT, {"request_name": "summarization", "opts": Q4_REQUEST}))

    urls: dict[str, str] = {}
    for stem, check, clip, spec in jobs:
        path = RAW / f"{stem}.json"
        if path.is_file() and not force:
            print(f"  {stem:40s} saved already (use --force to resend)")
            continue
        if clip not in urls:
            data = doubled_clip() if clip == LONG else (AUDIO / clip).read_bytes()
            urls[clip] = _upload(settings, data, clip)
        rec = http_call(Req("http", f"{ENDPOINTS['async']}/v2/transcript",
                            json={"audio_url": urls[clip], **spec["opts"]}, poll=True), settings)
        rec.update(check=check, clip=clip, request_name=spec["request_name"], recorded_at=now())
        write_json(path, rec, settings)
        fin = rec.get("final") or {}
        print(f"  {stem:40s} HTTP {(rec.get('response') or {}).get('status')} -> {fin.get('status', '-')}")
