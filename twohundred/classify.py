"""Turn saved raw records into the probe matrix. Pure functions, no network.

Decision table (pre-registered in docs/PROBES.md):

  rejected (HTTP 4xx, refused websocket, error frame, or transcript status "error")
      names the fix                      -> instructive_error
      otherwise                          -> opaque_error
  accepted (2xx / session opened / transcript completed)
      intended effect verified applied   -> honored
      effect not applied, warning given  -> accepted_with_warning
        (a new warning that names one of the probe's offender tokens and says it was skipped,
         ignored or unsupported means "not applied",
         even if the field is echoed back; added after the Phase 1 data was seen, see F1)
      effect not applied, no warning     -> silent_accept
      effect cannot be verified          -> accepted_unverified (or accepted_with_warning)
  control request did not succeed        -> invalid_control   (the row says nothing about the API)
  5xx, timeout, transport failure        -> inconclusive
  repeats disagree                       -> unstable

"Names the fix" is a case-insensitive substring match of the error text against the
probe's pre-registered fix_tokens. Where a probe has no fix tokens (a field that simply
does not exist), naming the offending field counts as naming the fix.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .probes import APPLIED, NOT_APPLIED, UNVERIFIABLE, Probe

LOUD = ("instructive_error", "opaque_error")
QUIET = ("silent_accept", "accepted_with_warning")
SIGNAL_NAME = re.compile(r"warn|deprecat|sunset|unknown", re.I)
NORMAL_CLOSE = (None, 1000, 1001)
# Words that say an input was dropped. A deprecation notice ("will return an error starting...")
# names the field too but does not say it was dropped, so it does not match.
NOT_USED = re.compile(r"skip|ignor|not (?:a )?supported|unsupported|not used|not applied", re.I)


@dataclass
class Outcome:
    state: str            # rejected | accepted | inconclusive
    stage: str = ""       # request | upload | processing | handshake | session
    code: str = ""        # "HTTP 400", "close 3007", ...
    error_text: str = ""


def _dump(obj: Any) -> str:
    return obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)


def _text(obj: Any) -> str:
    """Error bodies as plain text: every key and leaf value, without JSON escaping."""
    if isinstance(obj, dict):
        return " ".join(f"{k}: {_text(v)}" for k, v in obj.items())
    if isinstance(obj, list):
        return " ".join(_text(v) for v in obj)
    return "" if obj is None else str(obj)


def http_outcome(rec: dict) -> Outcome:
    pre = (rec.get("pre") or {}).get("response")
    if pre and pre.get("status", 0) >= 400:
        if pre["status"] >= 500:
            return Outcome("inconclusive", "upload", f"HTTP {pre['status']}")
        return Outcome("rejected", "upload", f"HTTP {pre['status']}", _text(pre.get("json") or pre.get("text") or ""))
    resp = rec.get("response")
    if resp is None:
        return Outcome("inconclusive", "request", "no response", rec.get("transport_error", ""))
    status = resp.get("status", 0)
    body = _text(resp.get("json") or resp.get("text") or "")
    if status >= 500 or 300 <= status < 400:
        return Outcome("inconclusive", "request", f"HTTP {status}", body)
    if status >= 400:
        return Outcome("rejected", "request", f"HTTP {status}", body)
    fin = rec.get("final")
    if fin is not None:
        if fin.get("poll_timeout") or "poll_http_status" in fin:
            return Outcome("inconclusive", "processing", "poll did not finish", _dump(fin)[:500])
        if fin.get("status") == "error":
            return Outcome("rejected", "processing", f"HTTP {status}, then status=error", str(fin.get("error", "")))
    return Outcome("accepted", "request", f"HTTP {status}")


def ws_outcome(rec: dict) -> Outcome:
    ws = rec.get("ws") or {}
    hs = ws.get("handshake_status")
    if hs not in (None, 101):
        if hs >= 500:
            return Outcome("inconclusive", "handshake", f"HTTP {hs}", ws.get("handshake_body", ""))
        return Outcome("rejected", "handshake", f"HTTP {hs}", ws.get("handshake_body", ""))
    if ws.get("handshake_error"):
        return Outcome("rejected", "handshake", "handshake failed", ws["handshake_error"])
    if rec.get("transport_error"):
        return Outcome("inconclusive", "handshake", "transport error", rec["transport_error"])
    msgs = [m for m in ws.get("messages") or [] if isinstance(m, dict)]
    opened = "begin" in ws or any(m.get("message_type") == "SessionBegins" for m in msgs)
    errors = [m for m in msgs if str(m.get("type", "")).lower() == "error" or "error" in m]
    code = ws.get("server_close_code")
    reason = ws.get("server_close_reason") or ""
    stage = "session" if opened else "handshake"
    if errors:
        label = f"error frame, close {code}" if code else "error frame"
        return Outcome("rejected", stage, label, _text(errors) + " " + reason)
    if ws.get("closed_by_server") and code not in NORMAL_CLOSE:
        if code in (1011, 1012, 1013, 3005):
            return Outcome("inconclusive", stage, f"close {code}", reason)
        return Outcome("rejected", stage, f"close {code}", reason)
    if opened:
        return Outcome("accepted", "session", "session opened")
    return Outcome("inconclusive", "handshake", "no Begin received", reason)


def outcome(rec: dict) -> Outcome:
    return ws_outcome(rec) if (rec.get("request") or {}).get("kind") == "ws" else http_outcome(rec)


def _walk(obj: Any, path: str = ""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _walk(v, f"{path}.{k}" if path else str(k))
            if SIGNAL_NAME.search(str(k)) and v not in (None, "", [], {}, False):
                yield path + "." + str(k) if path else str(k), v
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk(v, f"{path}[{i}]")


def signals(rec: dict) -> set[str]:
    """Every warning-like channel present in a response: headers, JSON keys, websocket frames."""
    found: set[str] = set()
    resp = rec.get("response") or {}
    for name, value in (resp.get("headers") or {}).items():
        if SIGNAL_NAME.search(name) and str(value).strip():
            found.add(f"header {name}: {value}")
    for src in (resp.get("json"), rec.get("final")):
        for path, value in _walk(src):
            found.add(f"body {path}: {_dump(value)[:300]}")
    for m in (rec.get("ws") or {}).get("messages") or []:
        if not isinstance(m, dict):
            continue
        if SIGNAL_NAME.search(str(m.get("type", ""))):
            found.add(f"frame {_dump(m)[:300]}")
        for path, value in _walk(m):
            found.add(f"frame {path}: {_dump(value)[:300]}")
    return found


def _has(text: str, tokens: list[str]) -> bool:
    low = text.lower()
    return any(t.lower() in low for t in tokens)


@dataclass
class Verdict:
    cls: str
    code: str = ""
    stage: str = ""
    names_offender: bool | None = None
    names_fix: bool | None = None
    effect: str = ""
    evidence: str = ""
    signals: list[str] = field(default_factory=list)
    error_excerpt: str = ""


def classify_one(probe: Probe, mistake: dict, control: dict | None, aux: dict) -> Verdict:
    """Classify a single (mistake, control) pair of records."""
    if probe.build is not None and control is not None:
        c = outcome(control)
        if c.state != "accepted":
            return Verdict("invalid_control", c.code, c.stage,
                           evidence=f"the corrected request was not accepted ({c.state}): {c.error_text[:300]}")
    o = outcome(mistake)
    if o.state == "inconclusive":
        return Verdict("inconclusive", o.code, o.stage, evidence=o.error_text[:300])
    if o.state == "rejected":
        names_offender = _has(o.error_text, probe.offender_tokens) if probe.offender_tokens else None
        names_fix = _has(o.error_text, probe.fix_tokens) if probe.fix_tokens else None
        instructive = names_fix if probe.fix_tokens else bool(names_offender)
        return Verdict("instructive_error" if instructive else "opaque_error", o.code, o.stage,
                       names_offender, names_fix, error_excerpt=o.error_text[:400])

    # accepted
    sig = signals(mistake) - (signals(control) if control else set())
    if probe.effect is not None:
        effect, evidence = probe.effect(mistake, aux)
    else:
        effect, evidence = UNVERIFIABLE, "no server-side echo for this input"
    if probe.by_construction:
        effect, evidence = NOT_APPLIED, f"not part of the public API ({evidence})"
    elif effect == APPLIED and probe.offender_tokens:
        # A warning that names the input overrides an echo (rule added 2026-11-15, after the
        # Phase 1 data was seen: `async.word_boost` echoed the field and also said it was skipped).
        named = sorted(s for s in sig
                       if _has(s.split(": ", 1)[-1], probe.offender_tokens) and NOT_USED.search(s.split(": ", 1)[-1]))
        if named:
            effect, evidence = NOT_APPLIED, f"a warning names the input as not used ({named[0][:200]}); {evidence}"
    elif effect != UNVERIFIABLE and probe.control_effect is not None and control is not None:
        c_effect, c_evidence = probe.control_effect(control, aux)
        if c_effect != APPLIED:
            effect = UNVERIFIABLE
            evidence = f"control did not show the effect ({c_evidence}); mistake: {evidence}"
    if effect == APPLIED:
        cls = "honored"
    elif sig:
        cls = "accepted_with_warning"
    elif effect == NOT_APPLIED:
        cls = "silent_accept"
    else:
        cls = "accepted_unverified"
    return Verdict(cls, o.code, o.stage, effect=effect, evidence=evidence, signals=sorted(sig))


def classify(probe: Probe, mistakes: list[dict], controls: list[dict], aux: dict) -> dict[str, Any]:
    """Aggregate the repeats of one probe into a matrix row."""
    if not mistakes:
        return {"id": probe.id, "class": "not_run", "reps": 0}
    verdicts = []
    for i, m in enumerate(mistakes):
        if m.get("skipped"):
            return {"id": probe.id, "class": "skipped", "reps": 0, "evidence": m["skipped"]}
        ctl = controls[min(i, len(controls) - 1)] if controls else None
        verdicts.append(classify_one(probe, m, ctl, aux))
    classes = sorted({v.cls for v in verdicts})
    row = asdict(verdicts[0])
    row["class"] = classes[0] if len(classes) == 1 else "unstable"
    row.pop("cls")
    if len(classes) > 1:
        row["evidence"] = "repeats disagree: " + ", ".join(v.cls for v in verdicts)
    row.update(id=probe.id, reps=len(mistakes), per_rep=[v.cls for v in verdicts])
    row["matches_skill"] = matches_doc(probe.documented, row["class"], row.get("stage", ""))
    return row


def matches_doc(documented: str, cls: str, stage: str) -> str:
    """Does the observed class agree with what the skill says the API does?"""
    if documented == "unspecified" or cls in ("invalid_control", "inconclusive", "unstable", "skipped", "not_run"):
        return "n/a"
    expect = {
        "reject": cls in LOUD,
        "silent": cls == "silent_accept",
        "warn": cls == "accepted_with_warning",
        "honored": cls == "honored",
        "late-fail": cls in LOUD and stage in ("processing", "session"),
    }[documented]
    if not expect and cls == "accepted_unverified" and documented in ("honored", "silent"):
        return "consistent (not verifiable)"
    return "yes" if expect else "NO"


# ---------------------------------------------------------------- loading raw records


def load_raw(raw_dir: Path) -> tuple[dict[str, dict[str, list[dict]]], dict]:
    """Returns ({probe_id: {"mistake": [...], "control": [...]}}, aux)."""
    by_probe: dict[str, dict[str, list[dict]]] = {}
    baseline: dict[str, dict] = {}
    for path in sorted(raw_dir.glob("*.json")):
        rec = json.loads(path.read_text(encoding="utf-8"))
        if path.name.startswith("_baseline."):
            baseline[rec["fixture"]] = rec
            continue
        if path.name.startswith("_"):
            continue
        slot = by_probe.setdefault(rec["probe_id"], {"mistake": [], "control": []})
        slot[rec["variant"]].append(rec)
    for slot in by_probe.values():
        for recs in slot.values():
            recs.sort(key=lambda r: r["rep"])
    return by_probe, {"baseline": baseline}
