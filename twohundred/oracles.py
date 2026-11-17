"""Outcome oracles for the four Phase 2 tasks. Pure functions over a run directory; no network.

Part of the pre-registration: committed before any agent run (Phase 2 build list, step 4).

A run directory holds (written by runner/run_one.py, or by `make oracle-check`):

    contract.json            exit code of the harness's own ./run.sh run, timeout flag
    out.json                 what the code under test wrote
    out.json.frames.jsonl    T1 only: every server message, as logged by the code under test
    server/transcript.json   async tasks: GET /v2/transcript/{transcript_id}, fetched by the harness
    workspace/               the files the agent left

Principle (decision 26): `outcome` checks what the server produced, not which parameter was
set. Where the server's own text can show the effect (redaction, spelling, key terms), the text
decides. Where it cannot (streaming settings), the configuration echo decides. A client-side
fix that the server did not make (rewriting American spellings locally, say) does not pass.

`runs` follows Phase 3: ./run.sh exited 0 inside the timeout and out.json matches the schema.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .fixtures import contains_digits
from .config import SCRIPTS

ROOT = Path(__file__).resolve().parents[1]
TASKS = ROOT / "runner" / "tasks"

STREAM_MODELS = {"universal-3-6-pro", "universal-3-5-pro"}
REQUIRED_POLICIES = {"person_name", "phone_number", "us_social_security_number"}
DURATION_TOLERANCE_S = 2.0
DELETED_TEXT = "Deleted by user."  # what GET /v2/transcript/{id} returns after DELETE (seen in pilot v2)
# Secondary T1 check, reported separately and kept out of `outcome` (decision 16).
NON_V3_STREAM_NAMES = re.compile(r"word_boost|boost_param|end_utterance_silence_threshold|/v2/realtime")
SOURCE_SUFFIXES = {".py", ".js", ".mjs", ".cjs", ".ts", ".sh", ".go", ".rb", ".java", ".rs"}


def _gt(name: str) -> dict[str, Any]:
    return json.loads((SCRIPTS / f"{name}.json").read_text(encoding="utf-8"))["ground_truth"]


def _load(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _frames(path: Path) -> list[dict]:
    out = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return out
    for line in lines:
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


# ---------------------------------------------------------------- schema (the subset the tasks use)


def schema_errors(obj: Any, schema: dict, path: str = "$") -> list[str]:
    errs: list[str] = []
    types = schema.get("type")
    if types is not None:
        allowed = types if isinstance(types, list) else [types]
        pytype = {"object": dict, "array": list, "string": str, "integer": int, "null": type(None)}
        if not any(isinstance(obj, pytype[t]) and not (t == "integer" and isinstance(obj, bool)) for t in allowed):
            return [f"{path}: expected {allowed}, got {type(obj).__name__}"]
    if isinstance(obj, dict):
        for k in schema.get("required", []):
            if k not in obj:
                errs.append(f"{path}: missing `{k}`")
        for k, sub in (schema.get("properties") or {}).items():
            if k in obj:
                errs += schema_errors(obj[k], sub, f"{path}.{k}")
    if isinstance(obj, list):
        if len(obj) < schema.get("minItems", 0):
            errs.append(f"{path}: fewer than {schema['minItems']} items")
        for i, item in enumerate(obj):
            if "items" in schema:
                errs += schema_errors(item, schema["items"], f"{path}[{i}]")
    if isinstance(obj, str) and len(obj) < schema.get("minLength", 0):
        errs.append(f"{path}: shorter than {schema['minLength']}")
    return errs


def runs_verdict(task: str, run_dir: Path) -> dict[str, Any]:
    contract = _load(run_dir / "contract.json") or {}
    out = _load(run_dir / "out.json")
    schema = _load(TASKS / task / "schema.json") or {}
    problems = []
    if contract.get("exit_code") != 0:
        problems.append(f"exit code {contract.get('exit_code')}")
    if contract.get("timed_out"):
        problems.append("timed out")
    if contract.get("cap_hit"):
        problems.append(f"agent hit a cap ({contract['cap_hit']})")
    if out is None:
        problems.append("out.json missing or not JSON")
    else:
        problems += schema_errors(out, schema)
    return {"runs": not problems, "problems": problems}


# ---------------------------------------------------------------- per-task oracles


def _check(checks: dict, name: str, ok: bool, evidence: str) -> None:
    checks[name] = {"pass": bool(ok), "evidence": evidence}


def _out_text(out: Any, list_key: str) -> str:
    if not isinstance(out, dict):
        return ""
    parts = [str(out.get("text") or "")]
    for u in out.get(list_key) or []:
        if isinstance(u, dict):
            parts.append(str(u.get("text") or ""))
    return " ".join(parts)


def _server_common(checks: dict, out: Any, t: dict | None, fixture_seconds: float) -> None:
    _check(checks, "server_transcript_fetched", isinstance(t, dict) and bool(t.get("id")),
           "server/transcript.json present" if t else "no server transcript (id missing from out.json, or GET failed)")
    t = t or {}
    _check(checks, "server_completed", t.get("status") == "completed", f"status={t.get('status')!r}")
    oid = out.get("transcript_id") if isinstance(out, dict) else None
    _check(checks, "id_matches_output", bool(oid) and oid == t.get("id"), f"out.json id={oid!r}, server id={t.get('id')!r}")
    deleted = str(t.get("text") or "").strip() == DELETED_TEXT
    _check(checks, "transcript_not_deleted", not deleted,
           "the run deleted the transcript, so the server-side evidence is gone" if deleted else "not deleted")
    dur = t.get("audio_duration")
    ok = isinstance(dur, (int, float)) and abs(dur - fixture_seconds) <= DURATION_TOLERANCE_S
    _check(checks, "is_the_fixture", ok, f"audio_duration={dur!r}, fixture {fixture_seconds} s")


def oracle_t1(run_dir: Path) -> dict[str, Any]:
    gt = _gt("support_call")
    checks: dict[str, Any] = {}
    frames = _frames(run_dir / "out.json.frames.jsonl")
    begin = next((f for f in frames if f.get("type") == "Begin"), None)
    cfg = (begin or {}).get("configuration") or {}
    _check(checks, "begin_logged", begin is not None, f"{len(frames)} frames logged" + ("" if begin else ", no Begin"))
    _check(checks, "model_current_pro", cfg.get("model") in STREAM_MODELS, f"model={cfg.get('model')!r}")
    _check(checks, "medical_mode", cfg.get("domain") == "medical-v1", f"domain={cfg.get('domain')!r}")
    _check(checks, "speaker_labels", cfg.get("speaker_labels") is True, f"speaker_labels={cfg.get('speaker_labels')!r}")
    _check(checks, "redact_pii", cfg.get("redact_pii") is True, f"redact_pii={cfg.get('redact_pii')!r}")
    finals = [f for f in frames if f.get("type") == "Turn" and f.get("end_of_turn")]
    server_text = " ".join(str(f.get("transcript") or f.get("utterance") or "") for f in finals)
    _check(checks, "final_turns_logged", bool(finals), f"{len(finals)} final turns in frames")
    for label, digits in (("ssn", gt["ssn_digits"]), ("phone", gt["phone_digits"])):
        _check(checks, f"{label}_absent_server_turns", bool(finals) and not contains_digits(server_text, digits),
               "digits absent" if not contains_digits(server_text, digits) else "digits present")
    out = _load(run_dir / "out.json")
    out_text = _out_text(out, "turns")
    for label, digits in (("ssn", gt["ssn_digits"]), ("phone", gt["phone_digits"])):
        _check(checks, f"{label}_absent_output", bool(out_text.strip()) and not contains_digits(out_text, digits),
               "digits absent" if not contains_digits(out_text, digits) else "digits present")
    return {"outcome": all(c["pass"] for c in checks.values()), "checks": checks,
            "evidence_kind": "self-reported frames (decision 19)",
            "secondary": {"non_v3_stream_names": code_scan(run_dir / "workspace")}}


def oracle_t2(run_dir: Path) -> dict[str, Any]:
    gt = _gt("support_call")
    checks: dict[str, Any] = {}
    out = _load(run_dir / "out.json")
    t = _load(run_dir / "server" / "transcript.json")
    _server_common(checks, out, t, 50.71)
    t = t or {}
    _check(checks, "speaker_labels", t.get("speaker_labels") is True, f"speaker_labels={t.get('speaker_labels')!r}")
    _check(checks, "redact_pii", t.get("redact_pii") is True, f"redact_pii={t.get('redact_pii')!r}")
    pol = set(t.get("redact_pii_policies") or [])
    _check(checks, "policies_cover_request", REQUIRED_POLICIES <= pol, f"redact_pii_policies={sorted(pol)}")
    server_text = _out_text(t, "utterances")
    for label, digits in (("ssn", gt["ssn_digits"]), ("phone", gt["phone_digits"])):
        _check(checks, f"{label}_absent_server", bool(server_text.strip()) and not contains_digits(server_text, digits),
               "digits absent" if not contains_digits(server_text, digits) else "digits present")
    names_seen = [n for n in gt["person_names"] if n.lower() in server_text.lower()]
    _check(checks, "names_absent_server", bool(server_text.strip()) and not names_seen, f"names present: {names_seen}")
    speakers = {u.get("speaker") for u in (t.get("utterances") or []) if isinstance(u, dict)}
    _check(checks, "two_speakers_server", len(speakers) >= 2, f"speakers={sorted(map(str, speakers))}")
    out_text = _out_text(out, "utterances")
    for label, digits in (("ssn", gt["ssn_digits"]), ("phone", gt["phone_digits"])):
        _check(checks, f"{label}_absent_output", bool(out_text.strip()) and not contains_digits(out_text, digits),
               "digits absent" if not contains_digits(out_text, digits) else "digits present")
    return {"outcome": all(c["pass"] for c in checks.values()), "checks": checks, "evidence_kind": "server"}


def oracle_t3(run_dir: Path) -> dict[str, Any]:
    gt = _gt("verbatim_memo")
    checks: dict[str, Any] = {}
    out = _load(run_dir / "out.json")
    t = _load(run_dir / "server" / "transcript.json")
    _server_common(checks, out, t, 12.11)
    t = t or {}

    def forms(text: str) -> tuple[list[str], list[str]]:
        low = text.lower()
        hit = lambda ws: [w for w in ws if re.search(rf"\b{re.escape(w)}\b", low)]
        return hit(gt["british_spellings"]), hit(gt["american_spellings"])

    for where, text in (("server", str(t.get("text") or "")), ("output", _out_text(out, "_none"))):
        uk, us = forms(text)
        _check(checks, f"british_{where}", len(uk) >= 3 and len(us) <= 1, f"British {uk}, American {us}")
    return {"outcome": all(c["pass"] for c in checks.values()), "checks": checks, "evidence_kind": "server",
            "info": {"language_code": t.get("language_code"),
                     "language_detection_options": t.get("language_detection_options"),
                     "speech_model_used": t.get("speech_model_used")}}


def oracle_t4(run_dir: Path) -> dict[str, Any]:
    terms = _gt("product_memo")["key_terms"]
    checks: dict[str, Any] = {}
    out = _load(run_dir / "out.json")
    t = _load(run_dir / "server" / "transcript.json")
    _server_common(checks, out, t, 17.27)
    t = t or {}
    _check(checks, "speaker_labels", t.get("speaker_labels") is True, f"speaker_labels={t.get('speaker_labels')!r}")
    for where, text in (("server", str(t.get("text") or "")), ("output", _out_text(out, "utterances"))):
        found = [w for w in terms if w in text]  # exact spelling and case
        _check(checks, f"names_exact_{where}", len(found) == len(terms), f"{len(found)}/{len(terms)} exact: {found}")
    summary = out.get("summary") if isinstance(out, dict) else None
    _check(checks, "summary_present", isinstance(summary, str) and bool(summary.strip()),
           f"summary length {len(summary) if isinstance(summary, str) else 0}")
    su = (((t.get("speech_understanding") or {}).get("response") or {}).get("summarization") or {})
    kp = t.get("keyterms_prompt") or []
    return {"outcome": all(c["pass"] for c in checks.values()), "checks": checks, "evidence_kind": "server",
            "info": {"summary_source": "speech_understanding" if su.get("status") == "success" else "not on transcript "
                     "(LLM Gateway or other; self-reported)",
                     "keyterms_prompt": kp, "word_boost": t.get("word_boost"), "prompt_set": bool(t.get("prompt")),
                     "speech_model_used": t.get("speech_model_used"),
                     "warnings": (t.get("metadata") or {}).get("warnings")}}


ORACLES = {"t1_stream_clinic": oracle_t1, "t2_async_redact": oracle_t2,
           "t3_british": oracle_t3, "t4_legacy_upgrade": oracle_t4}


def code_scan(workspace: Path) -> list[str]:
    """T1 secondary: non-v3 streaming parameter names in the run's own source (not dependencies)."""
    hits = []
    if not workspace.is_dir():
        return hits
    for p in sorted(workspace.rglob("*")):
        if p.suffix not in SOURCE_SUFFIXES or not p.is_file():
            continue
        if any(part in {"node_modules", ".venv", "venv", "site-packages", "__pycache__"} for part in p.parts):
            continue
        for i, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            for m in NON_V3_STREAM_NAMES.finditer(line):
                hits.append(f"{p.relative_to(workspace).as_posix()}:{i}: {m.group(0)}")
    return hits


def grade(task: str, run_dir: Path) -> dict[str, Any]:
    r = runs_verdict(task, run_dir)
    o = ORACLES[task](run_dir)
    contract = _load(run_dir / "contract.json") or {}
    if contract.get("cap_hit") or contract.get("container_error"):
        o["outcome"] = False  # Phase 3 rule: a capped or broken run fails both, and stays in the denominator
    return {"task": task, "runs": r["runs"], "runs_problems": r["problems"], **o,
            "silent_failure": r["runs"] and not o["outcome"]}
