"""Phase 3 grading, offline: `python -m twohundred.phase3`.

Reads results/phase2/codex/runs (oracle grades), results/phase3/judge (looks_right) and the agent logs
(first working call), and writes:
  results/phase3/grades.csv      one row per run, the three verdicts and the timing fields
  results/phase3/headline.json   every number the write-up uses
  results/phase3/headline.md     the same, readable
  results/phase3/continuity.json the re-grade of the skill repo's committed eval outputs
  results/phase3/chart1_verdicts.svg, chart2_first_call.svg
"""
from __future__ import annotations

import csv
import json
import statistics
from pathlib import Path
from typing import Any

from .config import RESULTS
from .firstcall import first_working_call, total_tokens
from .oracles import grade
from .stats import wilson

RUNS = RESULTS / "phase2" / "codex" / "runs"
P3 = RESULTS / "phase3"
JUDGE = P3 / "judge"
CONT = P3 / "continuity"
SKILL = Path(__import__("os").environ.get("SKILL_REPO", RESULTS.parent.parent / "assemblyai-skill"))
CONDITIONS = ["bare", "skill", "skill+mcp"]
TASK_ORDER = ["t1_stream_clinic", "t2_async_redact", "t3_british", "t4_legacy_upgrade"]
FIELDS = ["task", "condition", "index", "looks_right", "runs", "outcome", "silent_failure", "failed_checks",
          "looks_right_pass2", "judge_agree", "assertions_failed_pass1", "first_call_s", "agent_s", "num_turns",
          "input_tokens", "cached_input_tokens", "output_tokens", "total_tokens", "cap_hit", "skill_read",
          "mcp_calls", "t1_non_v3_names"]


def _load(p: Path) -> Any:
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None


def judge_pass(task: str, cond: str, idx: str, k: int) -> dict | None:
    return _load(JUDGE / task / cond / idx / f"pass{k}.json")


def rows() -> list[dict[str, Any]]:
    out = []
    for meta_p in sorted(RUNS.glob("*/*/*/meta.json")):
        d = meta_p.parent
        task, cond, idx = d.relative_to(RUNS).parts
        meta = _load(meta_p)
        g = grade(task, d)
        j1, j2 = judge_pass(task, cond, idx, 1), judge_pass(task, cond, idx, 2)
        v1 = (j1 or {}).get("verdict")
        v2 = (j2 or {}).get("verdict")
        fc = first_working_call(d / "agent.jsonl")
        agent = meta.get("agent") or {}
        tok = total_tokens(meta)
        failed_assertions = [e["text"] for e in ((j1 or {}).get("expectations") or []) if not e.get("passed")]
        out.append({
            "task": task, "condition": cond, "index": int(idx),
            "looks_right": v1, "runs": g["runs"], "outcome": g["outcome"], "silent_failure": g["silent_failure"],
            "failed_checks": ";".join([k for k, v in (g.get("checks") or {}).items() if not v.get("pass")]
                                      + list(g.get("runs_problems") or [])),
            "looks_right_pass2": v2, "judge_agree": (v1 == v2) if v1 is not None and v2 is not None else None,
            "assertions_failed_pass1": " | ".join(failed_assertions),
            "first_call_s": fc["seconds"], "agent_s": agent.get("seconds"), "num_turns": agent.get("num_turns"),
            **tok, "cap_hit": agent.get("cap_hit"), "skill_read": agent.get("skill_read"),
            "mcp_calls": agent.get("mcp_calls"),
            "t1_non_v3_names": len((g.get("secondary") or {}).get("non_v3_stream_names") or []) if task == "t1_stream_clinic" else None,
        })
    return out


def rate(k: int, n: int) -> dict[str, Any]:
    lo, hi = wilson(k, n) if n else (None, None)
    return {"k": k, "n": n, "p": k / n if n else None, "lo": lo, "hi": hi}


def quartiles(xs: list[float]) -> dict[str, Any]:
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return {"n": 0}
    q = statistics.quantiles(xs, n=4, method="inclusive") if len(xs) > 1 else [xs[0]] * 3
    return {"n": len(xs), "median": statistics.median(xs), "q1": q[0], "q3": q[2], "min": xs[0], "max": xs[-1]}


def headline(rs: list[dict[str, Any]]) -> dict[str, Any]:
    h: dict[str, Any] = {"n_runs": len(rs), "by_condition": {}, "by_task_condition": {}, "judge": {}}
    for c in CONDITIONS:
        sub = [r for r in rs if r["condition"] == c]
        ran = [r for r in sub if r["runs"]]
        judged = [r for r in sub if r["looks_right"] is not None]
        h["by_condition"][c] = {
            "silent_failures": rate(sum(r["silent_failure"] for r in sub), len(sub)),
            "silent_failures_of_runs_that_ran": rate(sum(r["silent_failure"] for r in ran), len(ran)),
            "looks_right": rate(sum(bool(r["looks_right"]) for r in judged), len(judged)),
            "runs": rate(sum(bool(r["runs"]) for r in sub), len(sub)),
            "outcome": rate(sum(bool(r["outcome"]) for r in sub), len(sub)),
            "first_call_s": quartiles([r["first_call_s"] for r in sub]),
            "agent_s": quartiles([r["agent_s"] for r in sub]),
            "total_tokens": quartiles([r["total_tokens"] for r in sub]),
            "output_tokens": quartiles([r["output_tokens"] for r in sub]),
            "num_turns": quartiles([r["num_turns"] for r in sub]),
            "no_first_call": sum(r["first_call_s"] is None for r in sub),
            "caps_hit": sum(bool(r["cap_hit"]) for r in sub),
        }
        for t in TASK_ORDER:
            st = [r for r in sub if r["task"] == t]
            h["by_task_condition"][f"{t}|{c}"] = {
                "n": len(st), "looks_right": sum(bool(r["looks_right"]) for r in st),
                "runs": sum(bool(r["runs"]) for r in st), "outcome": sum(bool(r["outcome"]) for r in st),
                "silent_failures": sum(bool(r["silent_failure"]) for r in st),
                "first_call_median_s": quartiles([r["first_call_s"] for r in st]).get("median")}
    both = [r for r in rs if r["judge_agree"] is not None]
    h["judge"] = {"runs_judged_twice": len(both), "run_level_agreement": sum(r["judge_agree"] for r in both),
                  "unusable_pass1": sum(r["looks_right"] is None for r in rs)}
    agree_a = total_a = 0
    for r in rs:
        j1 = judge_pass(r["task"], r["condition"], f"{r['index']:02d}", 1) or {}
        j2 = judge_pass(r["task"], r["condition"], f"{r['index']:02d}", 2) or {}
        e1, e2 = j1.get("expectations") or [], j2.get("expectations") or []
        if len(e1) == len(e2) and e1:
            total_a += len(e1)
            agree_a += sum(bool(a.get("passed")) == bool(b.get("passed")) for a, b in zip(e1, e2))
    h["judge"].update(assertion_level_agreement=agree_a, assertions_compared=total_a)
    # looks right but not outcome, and outcome but not looks right
    h["looks_right_not_outcome"] = sum(bool(r["looks_right"]) and not r["outcome"] for r in rs)
    h["outcome_not_looks_right"] = sum(r["looks_right"] is False and r["outcome"] for r in rs)
    t1 = [r for r in rs if r["task"] == "t1_stream_clinic"]
    h["t1_non_v3_names_by_condition"] = {c: sum(1 for r in t1 if r["condition"] == c and r["t1_non_v3_names"])
                                         for c in CONDITIONS}
    return h


CLASS_ORDER = [("instructive_error", "instructive error"), ("opaque_error", "opaque error"),
               ("silent_accept", "silent accept"), ("accepted_with_warning", "accepted with warning"),
               ("honored", "accepted and applied"), ("accepted_unverified", "accepted, effect unverifiable")]
SURFACES = ["async", "streaming", "sync", "dictation", "tts"]


STREAM_MODEL_NAMES = ("u3-rt-pro", "u3-rt-pro-beta-1", "universal-3-6-pro", "universal-3-5-pro", "universal-3-6",
                      "universal-streaming-english", "universal-streaming-multilingual", "u3-pro", "whisper-rt")


def t1_models() -> list[dict[str, Any]]:
    """Which streaming model each T1 run asked for in its own source, and which one the server ran (Begin)."""
    import re
    pat = re.compile(r"""["'](""" + "|".join(map(re.escape, STREAM_MODEL_NAMES)) + r""")["']""")
    out = []
    for d in sorted((RUNS / "t1_stream_clinic").glob("*/*")):
        src = "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in (d / "workspace").rglob("*")
                        if p.is_file() and p.suffix in (".py", ".js", ".ts", ".mjs", ".sh")
                        and not {"node_modules", ".venv", "venv", "site-packages"} & set(p.parts))
        frames = (d / "out.json.frames.jsonl")
        begin = {}
        if frames.is_file():
            for line in frames.read_text(encoding="utf-8").splitlines():
                if '"Begin"' in line:
                    begin = json.loads(line)
                    break
        out.append({"condition": d.parent.name, "index": int(d.name),
                    "requested": ",".join(sorted(set(pat.findall(src)))) or "(none in source)",
                    "applied": (begin.get("configuration") or {}).get("model")})
    return out


def async_models() -> dict[str, int]:
    """Secondary, not part of outcome: speech_models requested and speech_model_used, from GET /v2/transcript."""
    agg: dict[str, int] = {}
    for p in sorted(RUNS.glob("t[234]_*/*/*/server/transcript.json")):
        t = _load(p) or {}
        task, cond = p.parts[-5], p.parts[-4]
        k = f"{cond}|{task}|{','.join(t.get('speech_models') or []) or '(none)'}|{t.get('speech_model_used')}"
        agg[k] = agg.get(k, 0) + 1
    return agg


CURRENT_STREAM = {"universal-3-6-pro", "universal-3-5-pro"}
CURRENT_ASYNC = {"universal-3-5-pro"}


def current_model(tm: list[dict[str, Any]]) -> dict[str, Any]:
    """Secondary, not part of outcome: did the run ask for a current Pro model?
    T1: the model named in the run's own source. T2-T4: the first entry of `speech_models` echoed by the server."""
    per: dict[str, list[int]] = {c: [0, 0] for c in CONDITIONS}
    for r in tm:
        req = set(r["requested"].split(","))
        per[r["condition"]][0] += bool(req) and req <= CURRENT_STREAM
        per[r["condition"]][1] += 1
    for p in sorted(RUNS.glob("t[234]_*/*/*/server/transcript.json")):
        t = _load(p) or {}
        first = (t.get("speech_models") or [None])[0]
        c = p.parts[-4]
        per[c][0] += first in CURRENT_ASYNC
        per[c][1] += 1
    return {c: rate(k, n) for c, (k, n) in per.items()}


def probe_matrix() -> dict[str, Any]:
    m = _load(RESULTS / "phase1" / "matrix.json")
    table = {label: {s: 0 for s in SURFACES} for _, label in CLASS_ORDER}
    names = dict(CLASS_ORDER)
    for r in m["rows"]:
        table[names[r["class"]]][r["surface"]] += 1
    return {"by_class_surface": table, "total": len(m["rows"])}


def continuity() -> dict[str, Any]:
    out: dict[str, Any] = {}
    for p in sorted(CONT.glob("*/*/pass1.json")):
        j1 = _load(p)
        j2 = _load(p.parent / "pass2.json") or {}
        ev, cfg = p.parent.parent.name, p.parent.name
        e1 = j1.get("expectations") or []
        graded = _load(SKILL / "assemblyai-workspace/iteration-1" / ev / cfg / "grading.json") or {}
        exp = graded.get("expectations") or []
        committed = {"assertions": len(exp), "passed": sum(bool(e.get("passed")) for e in exp)}
        out[f"{ev}|{cfg}"] = {"assertions": len(j1.get("assertions") or []),
                              "passed_pass1": sum(bool(e.get("passed")) for e in e1),
                              "passed_pass2": sum(bool(e.get("passed")) for e in (j2.get("expectations") or [])),
                              "failed_pass1": [e["text"] for e in e1 if not e.get("passed")],
                              "committed": committed}
    return out


def _fmt(r: dict[str, Any]) -> str:
    return f"{r['k']}/{r['n']} ({100 * r['p']:.1f}%, 95% CI {100 * r['lo']:.1f}-{100 * r['hi']:.1f}%)" if r["n"] else "n/a"


def write_md(h: dict[str, Any], cont: dict[str, Any]) -> str:
    pm = h["probe_matrix"]
    T = ["", "## T1: streaming model requested in the run's source vs applied by the server (Begin.configuration)", "",
         "| Condition | requested | applied | runs |", "|---|---|---|---|"]
    for k, v in sorted(h["t1_model_requested_applied"].items()):
        c, req, app = k.split("|")
        T.append(f"| {c} | {req} | {app} | {v} |")
    T += ["", "## Asked for a current Pro model (T1: model in the run's source; T2-T4: first `speech_models` entry; secondary, not in outcome)", "",
          "| Condition | runs |", "|---|---|"]
    for c, v in h["asked_for_current_model"].items():
        T.append(f"| {c} | {_fmt(v)} |")
    T += ["", "## Async tasks: speech_models requested and speech_model_used (GET /v2/transcript; secondary, not in outcome)", "",
          "| Condition | task | requested | used | runs |", "|---|---|---|---|---|"]
    for k, v in sorted(h["async_models"].items()):
        c, t, req, used = k.split("|")
        T.append(f"| {c} | {t} | {req} | {used} | {v} |")
    M = T + ["", "## Phase 1 probe matrix by class and surface (from results/phase1/matrix.json)", "",
         "| Class | " + " | ".join(SURFACES) + " | total |", "|---|" + "---|" * (len(SURFACES) + 1)]
    for label, row in pm["by_class_surface"].items():
        M.append(f"| {label} | " + " | ".join(str(row[s]) for s in SURFACES) + f" | {sum(row.values())} |")
    M.append("| total | " + " | ".join(str(sum(r[s] for r in pm["by_class_surface"].values())) for s in SURFACES)
             + f" | {pm['total']} |")
    L = ["# Phase 3 headline (generated by `python -m twohundred.phase3`; do not edit by hand)", "",
         f"{h['n_runs']} runs, Codex CLI `gpt-6.1-sol`. Intervals are Wilson 95%.", "",
         "| Condition | silent failures (of all runs) | silent failures (of runs that ran) | looks_right | runs | outcome |",
         "|---|---|---|---|---|---|"]
    for c, v in h["by_condition"].items():
        L.append(f"| {c} | {_fmt(v['silent_failures'])} | {_fmt(v['silent_failures_of_runs_that_ran'])} | "
                 f"{_fmt(v['looks_right'])} | {_fmt(v['runs'])} | {_fmt(v['outcome'])} |")
    L += ["", "| Condition | first working call, s (median [IQR]) | agent wall time, s (median) | total tokens (median) | output tokens (median) | tool calls (median) | no first call | caps hit |",
          "|---|---|---|---|---|---|---|---|"]
    for c, v in h["by_condition"].items():
        f = v["first_call_s"]
        L.append(f"| {c} | {f['median']:.0f} [{f['q1']:.0f}-{f['q3']:.0f}] | {v['agent_s']['median']:.0f} | "
                 f"{v['total_tokens']['median']:,.0f} | {v['output_tokens']['median']:,.0f} | {v['num_turns']['median']:.0f} | "
                 f"{v['no_first_call']} | {v['caps_hit']} |")
    L += ["", "Per task (directional, n = 10 per cell): looks_right / runs / outcome / silent failures / median first call s", "",
          "| Task | " + " | ".join(CONDITIONS) + " |", "|---|" + "---|" * len(CONDITIONS)]
    for t in TASK_ORDER:
        cells = []
        for c in CONDITIONS:
            v = h["by_task_condition"][f"{t}|{c}"]
            cells.append(f"{v['looks_right']}/{v['runs']}/{v['outcome']}/{v['silent_failures']} · {v['first_call_median_s']:.0f}s")
        L.append(f"| {t} | " + " | ".join(cells) + " |")
    j = h["judge"]
    L += ["", f"Judge: {j['run_level_agreement']}/{j['runs_judged_twice']} runs got the same verdict on both passes; "
              f"{j['assertion_level_agreement']}/{j['assertions_compared']} assertions agreed. Unusable pass-1 outputs: {j['unusable_pass1']}.",
          f"looks_right but not outcome: {h['looks_right_not_outcome']}. outcome but not looks_right: {h['outcome_not_looks_right']}.",
          f"T1 runs whose source contains a non-v3 streaming parameter name (secondary, not in outcome): {h['t1_non_v3_names_by_condition']}.",
          "", "## Continuity: the skill repo's committed eval outputs, re-graded against today's evals.json", "",
          "| Eval | Config | committed grading (their assertions) | today's assertions | passed (pass 1) | passed (pass 2) | failed (pass 1) |",
          "|---|---|---|---|---|---|---|"]
    for k, v in cont.items():
        ev, cfg = k.split("|")
        cm = v["committed"]
        L.append(f"| {ev} | {cfg} | {cm['passed']}/{cm['assertions']} | {v['assertions']} | {v['passed_pass1']} | "
                 f"{v['passed_pass2']} | {'; '.join(v['failed_pass1']) or '-'} |")
    return "\n".join(L + M) + "\n"


# ---------- charts (plain SVG, no plotting dependency) ----------

COLORS = {"looks_right": "#6b7fd7", "runs": "#3a9e8f", "outcome": "#d9822b"}
COND_LABEL = {"bare": "bare", "skill": "skill", "skill+mcp": "skill + docs MCP"}


def _svg(w: int, h: int, body: list[str]) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" '
            f'font-family="Helvetica, Arial, sans-serif" font-size="12">\n<rect width="{w}" height="{h}" fill="#ffffff"/>\n'
            + "\n".join(body) + "\n</svg>\n")


def chart1(h: dict[str, Any]) -> str:
    W, H, left, top, bottom, right = 640, 340, 56, 48, 56, 16
    pw, ph = W - left - right, H - top - bottom
    lo_axis = 0.5
    y = lambda p: top + ph * (1 - (p - lo_axis) / (1 - lo_axis))  # noqa: E731
    b = [f'<text x="{left}" y="20" font-size="14" font-weight="bold">Pass rate by condition (n = 40 each, Wilson 95% CI)</text>']
    for t in (0.5, 0.6, 0.7, 0.8, 0.9, 1.0):
        b.append(f'<line x1="{left}" x2="{W - right}" y1="{y(t):.1f}" y2="{y(t):.1f}" stroke="#e3e3e3"/>')
        b.append(f'<text x="{left - 6}" y="{y(t) + 4:.1f}" text-anchor="end" fill="#555">{int(t * 100)}%</text>')
    group_w = pw / len(CONDITIONS)
    bar_w = group_w / 5
    for gi, c in enumerate(CONDITIONS):
        v = h["by_condition"][c]
        gx = left + gi * group_w + bar_w
        for bi, key in enumerate(["looks_right", "runs", "outcome"]):
            r = v[key]
            x = gx + bi * bar_w
            yp = y(max(r["p"], lo_axis))
            b.append(f'<rect x="{x:.1f}" y="{yp:.1f}" width="{bar_w - 4:.1f}" height="{top + ph - yp:.1f}" fill="{COLORS[key]}"/>')
            cx = x + (bar_w - 4) / 2
            b.append(f'<line x1="{cx:.1f}" x2="{cx:.1f}" y1="{y(max(r["lo"], lo_axis)):.1f}" y2="{y(r["hi"]):.1f}" stroke="#222" stroke-width="1.2"/>')
            for e in (r["lo"], r["hi"]):
                b.append(f'<line x1="{cx - 4:.1f}" x2="{cx + 4:.1f}" y1="{y(max(e, lo_axis)):.1f}" y2="{y(max(e, lo_axis)):.1f}" stroke="#222"/>')
            b.append(f'<text x="{cx:.1f}" y="{top + ph - 6:.1f}" text-anchor="middle" font-size="11" font-weight="bold" fill="#ffffff">{r["k"]}</text>')
        b.append(f'<text x="{gx + 1.5 * bar_w - 2:.1f}" y="{top + ph + 18:.1f}" text-anchor="middle">{COND_LABEL[c]}</text>')
    lx = left
    for key, label in (("looks_right", "looks right (LLM reads code)"), ("runs", "runs (exit 0, valid output)"),
                       ("outcome", "outcome (server-side check)")):
        b.append(f'<rect x="{lx}" y="{H - 22}" width="10" height="10" fill="{COLORS[key]}"/>')
        b.append(f'<text x="{lx + 14}" y="{H - 13}">{label}</text>')
        lx += 190
    b.append(f'<text x="{W - right}" y="{top - 8}" text-anchor="end" font-size="10" fill="#555">y axis starts at 50%; bar labels are counts of 40</text>')
    return _svg(W, H, b)


def chart2(rs: list[dict[str, Any]], h: dict[str, Any]) -> str:
    W, H = 640, 370
    left, right, top = 120, 24, 48
    panel_h = 125
    b = [f'<text x="16" y="20" font-size="14" font-weight="bold">Time to first working call, and total tokens per run (n = 40 each)</text>']

    def panel(y0: float, key: str, title: str, scale: float, unit: str) -> None:
        vals = {c: [r[key] for r in rs if r["condition"] == c and r[key] is not None] for c in CONDITIONS}
        vmax = max(max(v) for v in vals.values()) * 1.05
        x = lambda v: left + (W - left - right) * v / vmax  # noqa: E731
        b.append(f'<text x="16" y="{y0 + 4}" font-weight="bold" fill="#333">{title}</text>')
        for i, c in enumerate(CONDITIONS):
            cy = y0 + 34 + i * 28
            b.append(f'<text x="{left - 8}" y="{cy + 4}" text-anchor="end">{COND_LABEL[c]}</text>')
            q = quartiles(vals[c])
            b.append(f'<line x1="{x(q["min"]):.1f}" x2="{x(q["max"]):.1f}" y1="{cy}" y2="{cy}" stroke="#bbb"/>')
            b.append(f'<rect x="{x(q["q1"]):.1f}" y="{cy - 7}" width="{x(q["q3"]) - x(q["q1"]):.1f}" height="14" fill="#d9822b" fill-opacity="0.35" stroke="#d9822b"/>')
            b.append(f'<line x1="{x(q["median"]):.1f}" x2="{x(q["median"]):.1f}" y1="{cy - 8}" y2="{cy + 8}" stroke="#222" stroke-width="2"/>')
            for v in vals[c]:
                b.append(f'<circle cx="{x(v):.1f}" cy="{cy}" r="1.6" fill="#222" fill-opacity="0.5"/>')
            b.append(f'<text x="{x(q["median"]):.1f}" y="{cy - 10}" text-anchor="middle" font-size="10">{q["median"] / scale:,.0f}{unit}</text>')
        axis_y = y0 + 34 + 3 * 28 - 8
        for frac in (0, 0.25, 0.5, 0.75, 1.0):
            v = vmax * frac
            b.append(f'<text x="{x(v):.1f}" y="{axis_y + 6}" text-anchor="middle" font-size="9" fill="#777">{v / scale:,.0f}{unit}</text>')

    panel(top, "first_call_s", "First working call (seconds after the agent starts)", 1, "s")
    panel(top + panel_h + 30, "total_tokens", "Total tokens per run (input + output; Codex reports usage only per run)", 1000, "k")
    b.append(f'<text x="16" y="{H - 8}" font-size="10" fill="#555">Box: interquartile range; bar: median; dots: runs. Rule: decision 36.</text>')
    return _svg(W, H, b)


def main() -> None:
    P3.mkdir(parents=True, exist_ok=True)
    rs = rows()
    with (P3 / "grades.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in rs:
            w.writerow({k: r.get(k) for k in FIELDS})
    h = headline(rs)
    h["probe_matrix"] = probe_matrix()
    tm = t1_models()
    with (P3 / "t1_models.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["condition", "index", "requested", "applied"])
        w.writeheader()
        w.writerows(tm)
    agg: dict[str, int] = {}
    for r in tm:
        k = f"{r['condition']}|{r['requested']}|{r['applied']}"
        agg[k] = agg.get(k, 0) + 1
    h["t1_model_requested_applied"] = agg
    h["async_models"] = async_models()
    h["asked_for_current_model"] = current_model(tm)
    cont = continuity()
    (P3 / "headline.json").write_text(json.dumps(h, indent=2) + "\n", encoding="utf-8", newline="")
    (P3 / "continuity.json").write_text(json.dumps(cont, indent=2) + "\n", encoding="utf-8", newline="")
    (P3 / "headline.md").write_text(write_md(h, cont), encoding="utf-8", newline="")
    (P3 / "chart1_verdicts.svg").write_text(chart1(h), encoding="utf-8", newline="")
    (P3 / "chart2_first_call.svg").write_text(chart2(rs, h), encoding="utf-8", newline="")
    print((P3 / "headline.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
