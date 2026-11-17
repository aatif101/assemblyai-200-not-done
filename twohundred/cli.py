"""Command line entry point:  python -m twohundred <command>

  plan [--kill]                 print every request that would be sent (offline, no key needed)
  fixtures build [--backend]    synthesise the audio fixtures
  fixtures validate             check against the live API that each clip carries its ground truth
  probes run [--kill]           send the probes and their controls; save raw scrubbed records
  probes classify [--kill]      build the matrix from saved records (offline)
  probes list                   write docs/PROBES.md (the pre-registered probe list)
  precheck run [--force]        live: the Phase 2 pre-checks for T3 and T4
  precheck decide               offline: decide Q3 and Q4 from saved pre-check records
  oracle-check                  live: each task's reference solution, right and wrong, graded by its oracle
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import requests

from . import fixtures as fx
from .classify import LOUD, QUIET, classify, load_raw
from .config import AUDIO, ENDPOINTS, PHASE1, ROOT, SKILL_COMMIT, SKILL_COMMIT_DATE, Settings, load_settings
from .probes import BY_ID, EN, KILL, PROBES, VERBATIM, Ctx, Probe, filler_count
from .transport import Req, _describe_request, execute, http_call

RAW = PHASE1 / "raw"
KILL_LOUD_THRESHOLD = 8  # pre-registered: >= 8 of 10 loud means "the API rejects loudly"


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_json(path: Path, obj: Any, settings: Settings) -> None:
    """The only way anything reaches results/: scrubbed, then checked again."""
    text = json.dumps(settings.scrubber.scrub_obj(obj), indent=2, ensure_ascii=False) + "\n"
    if settings.scrubber.contains_secret(text):  # cannot happen; refuse to write if it does
        raise RuntimeError(f"refusing to write {path.name}: a secret survived scrubbing")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------- live helpers


def make_ctx(settings: Settings, dry: bool = False) -> Ctx:
    state: dict[str, str] = {}

    def upload(fixture: str) -> str:
        if dry:
            return f"<upload_url of {fixture}>"
        r = requests.post(f"{ENDPOINTS['async']}/v2/upload",
                          headers={"Authorization": settings.api_key, "Content-Type": "application/octet-stream"},
                          data=(AUDIO / fixture).read_bytes(), timeout=120)
        if r.status_code != 200:
            sys.exit(f"Upload of {fixture} failed with HTTP {r.status_code}: "
                     f"{settings.scrubber.scrub(r.text[:300])}")
        return r.json()["upload_url"]

    def transcript_id() -> str:
        if dry:
            return "<id of a completed transcript>"
        if "tid" not in state:
            rec = baseline(settings, ctx, EN)
            state["tid"] = (rec.get("final") or {}).get("id", "")
        return state["tid"]

    ctx = Ctx(upload, transcript_id)
    return ctx


def baseline(settings: Settings, ctx: Ctx, fixture: str) -> dict:
    """Plain transcription of a fixture with no options. Cached on disk."""
    path = RAW / f"_baseline.{fixture}.json"
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    rec = http_call(Req("http", f"{ENDPOINTS['async']}/v2/transcript",
                        json={"audio_url": ctx.audio_url(fixture)}, poll=True), settings)
    rec.update(fixture=fixture, recorded_at=now())
    write_json(path, rec, settings)
    return rec


def brief(rec: dict) -> str:
    """One status word for the console. Never prints bodies."""
    if rec.get("skipped"):
        return "skipped"
    if "ws" in rec:
        ws = rec["ws"]
        if ws.get("handshake_status") not in (None, 101):
            return f"handshake HTTP {ws['handshake_status']}"
        tail = f", server close {ws['server_close_code']}" if ws.get("closed_by_server") else ""
        return ("Begin received" if "begin" in ws else "no Begin") + tail
    resp = rec.get("response")
    if not resp:
        return "no response"
    fin = rec.get("final")
    return f"HTTP {resp['status']}" + (f" -> {fin.get('status', '?')}" if fin else "")


# ---------------------------------------------------------------- commands


def cmd_plan(args: argparse.Namespace) -> int:
    settings = load_settings(require_key=False)
    ctx = make_ctx(settings, dry=True)
    for p in (KILL if args.kill else PROBES):
        print(f"\n## {p.id}  [{p.surface}]  {p.mistake}")
        for label, correct in (("mistake", False), ("control", True)):
            req = p.build(ctx, correct)
            print(f"  {label}: " + (json.dumps(_describe_request(req), ensure_ascii=False) if req else "(none)"))
    return 0


def cmd_fixtures_build(args: argparse.Namespace) -> int:
    settings = load_settings(require_key=args.backend == "aai-tts")
    manifest = fx.build(args.backend, settings)
    for name, meta in manifest["files"].items():
        print(f"  {name:22s} {meta.get('seconds', '-'):>6} s   sha256 {meta['sha256'][:12]}…")
    print(f"Built {len(manifest['files'])} files with the {args.backend} backend. "
          "Run `make validate-fixtures` before using them.")
    return 0


def cmd_fixtures_validate(args: argparse.Namespace) -> int:
    settings = load_settings()
    problems = fx.verify_manifest()
    if problems:
        sys.exit("Fixture files do not match the manifest:\n  " + "\n  ".join(problems))
    ctx = make_ctx(settings)
    gt = {s["id"]: s["ground_truth"] for s in fx.scripts()}
    base = f"{ENDPOINTS['async']}/v2/transcript"

    def run(fixture: str, **body: Any) -> dict:
        rec = http_call(Req("http", base, json={"audio_url": ctx.audio_url(fixture), **body}, poll=True), settings)
        return rec.get("final") or {}

    checks: list[dict[str, Any]] = []

    def check(fixture: str, name: str, ok: bool, detail: str, required: bool = True) -> None:
        checks.append({"fixture": fixture, "check": name, "passed": bool(ok), "required": required, "detail": detail})
        print(f"  {'PASS' if ok else ('FAIL' if required else 'note')}  {fixture:18s} {name}: {detail}")

    call = gt["support_call"]
    plain = run("support_call.wav", speaker_labels=True)
    text = plain.get("text") or ""
    check("support_call", "SSN is audible to the API (no redaction)",
          fx.contains_digits(text, call["ssn_digits"]), f"digits found: {fx.contains_digits(text, call['ssn_digits'])}")
    speakers = sorted({u.get("speaker") for u in plain.get("utterances") or []})
    check("support_call", "two speakers separated", len(speakers) == call["speakers"], f"speakers={speakers}")
    red = run("support_call.wav", redact_pii=True, redact_pii_sub="hash",
              redact_pii_policies=["us_social_security_number", "phone_number", "person_name"])
    rtext = red.get("text") or ""
    check("support_call", "SSN absent when redaction is on", bool(rtext) and not fx.contains_digits(rtext, call["ssn_digits"]),
          f"status={red.get('status')}, SSN digits present={fx.contains_digits(rtext, call['ssn_digits'])}")
    check("support_call", "phone number absent when redaction is on",
          bool(rtext) and not fx.contains_digits(rtext, call["phone_digits"]),
          f"phone digits present={fx.contains_digits(rtext, call['phone_digits'])}", required=False)

    terms = gt["product_memo"]["key_terms"]
    for label, body in (("with keyterms_prompt", {"keyterms_prompt": terms}), ("with no options", {})):
        t = (run("product_memo.wav", **body).get("text") or "").lower()
        hit = [k for k in terms if k.lower() in t]
        check("product_memo", f"product names spelled exactly, {label}", len(hit) == len(terms),
              f"{len(hit)}/{len(terms)} ({', '.join(hit) or 'none'})", required=label.startswith("with key"))

    v = gt["verbatim_memo"]
    with_d = filler_count(run("verbatim_memo.wav", disfluencies=True).get("text"))
    without = filler_count(run("verbatim_memo.wav").get("text"))
    check("verbatim_memo", "fillers kept with disfluencies=true", with_d >= 3 and with_d > without,
          f"{with_d} fillers with disfluencies=true, {without} with no options, {v['fillers_spoken']} spoken")

    it = (run("short_it.wav", language_code="it").get("text") or "").lower()
    check("short_it", "Italian content transcribed", all(w in it for w in gt["short_it"]["must_contain"]),
          f"contains {[w for w in gt['short_it']['must_contain'] if w in it]}")
    en = (run("short_en.wav").get("text") or "").lower()
    check("short_en", "English content transcribed", all(w.lower() in en for w in gt["short_en"]["must_contain"]),
          f"contains {[w for w in gt['short_en']['must_contain'] if w.lower() in en]}")

    failed = [c for c in checks if c["required"] and not c["passed"]]
    manifest = json.loads(fx.MANIFEST.read_text(encoding="utf-8"))
    write_json(PHASE1 / "fixture_validation.json",
               {"validated_at": now(), "backend": manifest["backend"], "checks": checks,
                "passed": not failed}, settings)
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks OK; {len(failed)} required checks failed. "
          f"Written to results/phase1/fixture_validation.json")
    if failed:
        print("A failed required check means that clip cannot serve as ground truth. Rebuild with\n"
              "  make fixtures BACKEND=aai-tts && make validate-fixtures")
    return 1 if failed else 0


def select(args: argparse.Namespace) -> list[Probe]:
    chosen = KILL if args.kill else PROBES
    if getattr(args, "only", None):
        ids = args.only.split(",")
        unknown = [i for i in ids if i not in BY_ID]
        if unknown:
            sys.exit(f"unknown probe id(s): {', '.join(unknown)}")
        chosen = [BY_ID[i] for i in ids]
    if getattr(args, "surface", None):
        chosen = [p for p in chosen if p.surface == args.surface]
    return chosen


def cmd_probes_run(args: argparse.Namespace) -> int:
    settings = load_settings()
    problems = fx.verify_manifest()
    if problems:
        sys.exit("Fixture files do not match the manifest:\n  " + "\n  ".join(problems))
    ctx = make_ctx(settings)
    probes = select(args)
    RAW.mkdir(parents=True, exist_ok=True)
    if any(p.id == "async.prompt.instructions" for p in probes):
        baseline(settings, ctx, VERBATIM)

    control_cache: dict[str, dict] = {}
    total = sum(args.repeats * (2 if p.build(make_ctx(settings, dry=True), True) else 1) for p in probes)
    n = 0
    for p in probes:
        missing = [f for f in p.fixtures if not (AUDIO / f).is_file()]
        for variant, correct in (("control", True), ("mistake", False)):
            for rep in range(args.repeats):
                path = RAW / f"{p.id}.{variant}.{rep}.json"
                if missing:
                    if variant == "mistake":
                        write_json(path, {"probe_id": p.id, "variant": variant, "rep": rep,
                                          "skipped": f"fixture missing: {', '.join(missing)}"}, settings)
                    continue
                req = p.build(ctx, correct)
                if req is None:
                    continue
                n += 1
                if path.is_file() and not args.force:
                    print(f"[{n}/{total}] {p.id} {variant}#{rep}: already recorded")
                    continue
                key = json.dumps(_describe_request(req), sort_keys=True) + f"#{rep}"
                if correct and key in control_cache:
                    rec = {**control_cache[key], "reused_control": True}
                else:
                    rec = execute(req, settings)
                    if correct:
                        control_cache[key] = rec
                rec = {"probe_id": p.id, "variant": variant, "rep": rep, "surface": p.surface,
                       "recorded_at": now(), "skill_commit": SKILL_COMMIT, **rec}
                write_json(path, rec, settings)
                print(f"[{n}/{total}] {p.id} {variant}#{rep}: {brief(rec)}")
    print(f"\nRaw records are in {RAW}/ . Next: make classify")
    return 0


CLASS_LABEL = {
    "instructive_error": "instructive error", "opaque_error": "opaque error",
    "silent_accept": "silent accept", "accepted_with_warning": "accepted with warning",
    "honored": "accepted and applied", "accepted_unverified": "accepted, effect unverifiable",
    "invalid_control": "invalid (control failed)", "inconclusive": "inconclusive",
    "unstable": "unstable across repeats", "skipped": "skipped", "not_run": "not run",
}
CLASS_ORDER = list(CLASS_LABEL)


def cmd_probes_classify(args: argparse.Namespace) -> int:
    settings = load_settings(require_key=False)
    by_probe, aux = load_raw(RAW)
    probes = select(args)
    rows = []
    for p in probes:
        slot = by_probe.get(p.id, {"mistake": [], "control": []})
        row = classify(p, slot["mistake"], slot["control"], aux)
        row.update(surface=p.surface, mistake=p.mistake, origin=p.origin, documented=p.documented)
        rows.append(row)

    tag = "killtest" if args.kill else "matrix"
    decided = [r for r in rows if r["class"] in LOUD + QUIET + ("honored", "accepted_unverified")]
    counts = Counter(r["class"] for r in rows)
    by_surface: dict[str, Counter] = {}
    for r in rows:
        by_surface.setdefault(r["surface"], Counter())[r["class"]] += 1
    summary: dict[str, Any] = {
        "generated_at": now(), "skill_commit": SKILL_COMMIT, "probes": len(rows), "decided": len(decided),
        "counts": {c: counts[c] for c in CLASS_ORDER if counts[c]},
        "by_surface": {s: {c: n for c, n in sorted(cn.items())} for s, cn in sorted(by_surface.items())},
        "disagrees_with_skill": [r["id"] for r in rows if r.get("matches_skill") == "NO"],
    }
    if args.kill:
        loud = sum(1 for r in rows if r["class"] in LOUD)
        undecided = [r["id"] for r in rows if r not in decided]
        summary["kill_test"] = {
            "loud": loud, "of": len(rows), "undecided": undecided, "threshold": KILL_LOUD_THRESHOLD,
            "verdict": ("INCOMPLETE: re-run the undecided probes before deciding" if undecided else
                        "PIVOT: the API rejects loudly" if loud >= KILL_LOUD_THRESHOLD else
                        "PROCEED: enough requests are accepted without an error to measure"),
        }

    PHASE1.mkdir(parents=True, exist_ok=True)
    write_json(PHASE1 / f"{tag}.json", {"summary": summary, "rows": rows}, settings)
    cols = ["id", "surface", "mistake", "origin", "documented", "class", "code", "stage", "names_offender",
            "names_fix", "effect", "evidence", "signals", "matches_skill", "reps", "error_excerpt"]
    with (PHASE1 / f"{tag}.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({**r, "signals": " | ".join(r.get("signals") or [])})
    (PHASE1 / f"{tag}.md").write_text(render_md(rows, summary, args.kill), encoding="utf-8")

    print(render_console(rows, summary))
    print(f"\nWritten: results/phase1/{tag}.md, {tag}.csv, {tag}.json")
    return 0


def _cell(text: Any, limit: int = 140) -> str:
    s = "" if text is None else str(text)
    s = s.replace("|", "\\|").replace("\n", " ")
    return s if len(s) <= limit else s[: limit - 1] + "…"


def render_md(rows: list[dict], summary: dict, kill: bool) -> str:
    out = [f"# Probe matrix{' (kill test)' if kill else ''}", "",
           f"Generated {summary['generated_at']} from raw records in `results/phase1/raw/`. "
           f"Probe list drawn from AssemblyAI/assemblyai-skill at `{SKILL_COMMIT[:7]}` ({SKILL_COMMIT_DATE}).", "",
           "Classes: **instructive error** (rejected, and the message names the fix), **opaque error** "
           "(rejected, message does not), **silent accept** (accepted, intended effect not applied, no "
           "warning anywhere in the response), **accepted with warning**, **accepted and applied**.", "",
           "The `skill says` column is what AssemblyAI's own skill documents for that input; "
           "`agrees` compares it with what the API did.", "",
           "| Class | Probes |", "|---|---|"]
    out += [f"| {CLASS_LABEL[c]} | {n} |" for c, n in summary["counts"].items()]
    if "kill_test" in summary:
        k = summary["kill_test"]
        out += ["", f"**Kill test: {k['loud']} of {k['of']} rejected loudly "
                    f"(threshold for pivot: {k['threshold']}). {k['verdict']}.**"]
    for surface in ("async", "streaming", "sync", "dictation", "tts"):
        sub = [r for r in rows if r["surface"] == surface]
        if not sub:
            continue
        out += ["", f"## {surface}", "",
                "| Probe | Mistake | Skill says | Observed | Response | Evidence | Agrees |",
                "|---|---|---|---|---|---|---|"]
        for r in sub:
            ev = r.get("error_excerpt") or r.get("evidence") or ""
            if r.get("signals"):
                ev = f"{ev} · warning: {'; '.join(r['signals'])}"
            flags = ""
            if r["class"] in LOUD:
                flags = (f" (names input: {'yes' if r.get('names_offender') else 'no'}"
                         f"{'' if r.get('names_fix') is None else ', names fix: ' + ('yes' if r['names_fix'] else 'no')})")
            out.append(f"| `{r['id']}` | {_cell(r['mistake'], 90)} | {r['documented']} | "
                       f"**{CLASS_LABEL[r['class']]}**{flags} | {_cell(r.get('code'))} | {_cell(ev, 220)} | "
                       f"{r.get('matches_skill', 'n/a')} |")
    return "\n".join(out) + "\n"


def render_console(rows: list[dict], summary: dict) -> str:
    out = ["", f"{'probe':40s} {'skill says':12s} {'observed':30s} response"]
    for r in rows:
        out.append(f"{r['id']:40s} {r['documented']:12s} {CLASS_LABEL[r['class']]:30s} {r.get('code', '')}")
    out.append("")
    out += [f"  {CLASS_LABEL[c]:32s} {n}" for c, n in summary["counts"].items()]
    if summary["disagrees_with_skill"]:
        out.append(f"\nObserved behavior differs from the skill's description: {', '.join(summary['disagrees_with_skill'])}")
    if "kill_test" in summary:
        k = summary["kill_test"]
        out.append(f"\nKILL TEST: {k['loud']}/{k['of']} rejected loudly -> {k['verdict']}")
    return "\n".join(out)


def cmd_probes_list(args: argparse.Namespace) -> int:
    out = ["# Pre-registered probe list", "",
           f"{len(PROBES)} known-wrong requests, written before any was sent. Each is paired with a control: "
           "the same request with the mistake corrected. A probe counts only if its control succeeds.", "",
           f"Source for `skill` probes: AssemblyAI/assemblyai-skill at `{SKILL_COMMIT}` ({SKILL_COMMIT_DATE}). "
           "The quoted sentence is what that revision says about the input. `author` probes are generic "
           "mistakes added here and are not attributed to the skill.", "",
           "`Skill says` is the behavior the skill documents: reject, silent (ignored or redirected without "
           "an error), warn (accepted, with a warning channel), honored (still works), late-fail, or "
           "unspecified (listed as a mistake without saying how the API responds).", "",
           "Kill test probes are marked K.", "",
           "## How a probe is judged", "",
           "Decided mechanically by `twohundred/classify.py` from the saved records, never by reading "
           "and deciding:", "",
           "| Observation | Class |", "|---|---|",
           "| Rejected (HTTP 4xx, refused or error-closed websocket, transcript `status: error`) and the "
           "error text contains one of the probe's names-the-fix tokens | instructive error |",
           "| Rejected, no such token | opaque error |",
           "| Accepted, server-side evidence shows the intended effect was not applied, and no warning in "
           "any header, body field or frame that the control did not also carry | silent accept |",
           "| Accepted, effect not applied (or not verifiable), with such a warning | accepted with warning |",
           "| Accepted and the effect is verified applied | accepted and applied |",
           "| Accepted, nothing in the response can confirm or refute the effect | accepted, effect unverifiable |",
           "| The control request failed | invalid (says nothing about the API) |",
           "| 5xx, timeout, transport failure | inconclusive |", "",
           "Where a probe has no names-the-fix tokens (the input simply does not exist), an error that names "
           "the offending input counts as instructive. Server-side evidence means: the config echoed by "
           "`GET /v2/transcript/{id}`, `speech_model_used`, `metadata`, the streaming `Begin.configuration`, "
           "or the bytes returned. A parameter name that is not part of the public API is treated as not "
           "applied by construction.", "",
           f"Kill-test rule, fixed in advance: if {KILL_LOUD_THRESHOLD} or more of the 10 kill probes are "
           "rejected loudly, the premise fails and the study pivots to ranking error messages.", ""]
    for surface in ("async", "streaming", "sync", "dictation", "tts"):
        out += [f"## {surface}", "", "| | Probe | Mistake | Origin | Skill says | Names-the-fix tokens | Skill text |",
                "|---|---|---|---|---|---|---|"]
        for p in [p for p in PROBES if p.surface == surface]:
            src = f"{p.source_file}: “{p.source_quote}”" if p.source_quote else ""
            out.append(f"| {'K' if p.kill else ''} | `{p.id}` | {_cell(p.mistake, 100)} | {p.origin} | {p.documented} | "
                       f"{_cell(', '.join(p.fix_tokens) or '(names the input)')} | {_cell(src, 260)} |")
        out.append("")
    (ROOT / "docs" / "PROBES.md").write_text("\n".join(out), encoding="utf-8")
    print(f"Wrote docs/PROBES.md ({len(PROBES)} probes)")
    return 0


def cmd_precheck_run(args: argparse.Namespace) -> int:
    from . import precheck
    settings = load_settings()
    precheck.run(settings, write_json, now, force=args.force)
    return cmd_precheck_decide(args)


def cmd_precheck_decide(args: argparse.Namespace) -> int:
    from . import precheck
    settings = load_settings(require_key=False)
    q3_recs, q4_rec = precheck.load_records()
    if not q3_recs and q4_rec is None:
        sys.exit("No pre-check records under results/phase2/precheck/raw. Run `make precheck` first.")
    q3, q4 = precheck.decide_q3(q3_recs), precheck.decide_q4(q4_rec)
    write_json(precheck.OUT / "precheck.json", {"q3": q3, "q4": q4}, settings)
    md = precheck.report(q3, q4)
    (precheck.OUT / "precheck.md").write_text(md, encoding="utf-8")
    print(md)
    return 0


def cmd_oracle_check(args: argparse.Namespace) -> int:
    from .rundir import oracle_check
    return oracle_check(load_settings())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="twohundred", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("plan"); sp.add_argument("--kill", action="store_true"); sp.set_defaults(fn=cmd_plan)

    f = sub.add_parser("fixtures").add_subparsers(dest="sub", required=True)
    sp = f.add_parser("build"); sp.add_argument("--backend", choices=["espeak", "aai-tts"], default="espeak")
    sp.set_defaults(fn=cmd_fixtures_build)
    sp = f.add_parser("validate"); sp.set_defaults(fn=cmd_fixtures_validate)

    p = sub.add_parser("probes").add_subparsers(dest="sub", required=True)
    sp = p.add_parser("run")
    sp.add_argument("--kill", action="store_true"); sp.add_argument("--only"); sp.add_argument("--surface")
    sp.add_argument("--repeats", type=int, default=2); sp.add_argument("--force", action="store_true")
    sp.set_defaults(fn=cmd_probes_run)
    sp = p.add_parser("classify")
    sp.add_argument("--kill", action="store_true"); sp.add_argument("--only"); sp.add_argument("--surface")
    sp.set_defaults(fn=cmd_probes_classify)
    sp = p.add_parser("list"); sp.set_defaults(fn=cmd_probes_list)

    pc = sub.add_parser("precheck").add_subparsers(dest="sub", required=True)
    sp = pc.add_parser("run"); sp.add_argument("--force", action="store_true"); sp.set_defaults(fn=cmd_precheck_run)
    sp = pc.add_parser("decide"); sp.set_defaults(fn=cmd_precheck_decide)

    sp = sub.add_parser("oracle-check"); sp.set_defaults(fn=cmd_oracle_check)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
