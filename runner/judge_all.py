"""Judge every Codex sweep run twice, plus the skill repo's committed eval outputs (continuity).

    python runner/judge_all.py [--parallel 4] [--only sweep|continuity]

Writes results/phase3/judge/<task>/<condition>/<idx>/pass<k>.json and
results/phase3/continuity/<eval>/<config>/pass<k>.json. Resumable: a pass with usable output is skipped.
Stops cleanly on the owner's Codex usage limit (same rule as decision 34).
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from twohundred import judge  # noqa: E402
from twohundred.agentrun import IMAGE, image_digest  # noqa: E402

RUNS = ROOT / "results/phase2/codex/runs"
OUT = ROOT / "results/phase3/judge"
CONT_OUT = ROOT / "results/phase3/continuity"
TASKS = ROOT / "runner/tasks"
SKILL = Path(__import__("os").environ.get("SKILL_REPO", ROOT.parent / "assemblyai-skill"))
PASSES = (1, 2)
STOP = threading.Event()


def sweep_jobs():
    for meta in sorted(RUNS.glob("*/*/*/meta.json")):
        d = meta.parent
        task, cond, idx = d.relative_to(RUNS).parts
        rubric = json.loads((TASKS / task / "rubric.json").read_text(encoding="utf-8"))
        assertions = [a["text"] for a in rubric["assertions"]]
        prompt_md = (TASKS / task / "prompt.md").read_text(encoding="utf-8")
        ws = d / "workspace"
        files = judge.source_files(ws)
        # decision 37: also show the judge any workspace file the task prompt names (T4: README.md)
        named = [ws / n for n in judge.NAMED_IN_PROMPT if n in prompt_md and (ws / n).is_file()]
        files = sorted(set(files) | set(named))
        prompt = judge.build_prompt(prompt_md, assertions, files, ws)
        for k in PASSES:
            yield (OUT / task / cond / idx / f"pass{k}.json", prompt, assertions, f"{task}-{cond}-{idx}-p{k}",
                   {"run": f"{task}/{cond}/{idx}", "files": [f.relative_to(ws).as_posix() for f in files]})


def continuity_jobs():
    evals = json.loads((SKILL / "skills/assemblyai/evals/evals.json").read_text(encoding="utf-8"))["evals"]
    by_id = {e["id"]: e for e in evals}
    for ev_dir in sorted((SKILL / "assemblyai-workspace/iteration-1").glob("eval-*")):
        md = json.loads((ev_dir / "eval_metadata.json").read_text(encoding="utf-8"))
        cur = by_id[md["eval_id"]]
        assertions = [a["text"] if isinstance(a, dict) else str(a)
                      for a in (cur.get("assertions") or cur.get("expectations"))]
        for cfg in ("with_skill", "without_skill"):
            outs = ev_dir / cfg / "outputs"
            files = judge.source_files(outs)
            prompt = judge.build_prompt(cur.get("prompt") or md["prompt"], assertions, files, outs)
            for k in PASSES:
                yield (CONT_OUT / ev_dir.name / cfg / f"pass{k}.json", prompt, assertions,
                       f"cont-{md['eval_id']}-{cfg}-p{k}",
                       {"eval": ev_dir.name, "config": cfg, "files": [f.relative_to(outs).as_posix() for f in files]})


def usable(path: Path, n: int) -> bool:
    if not path.is_file():
        return False
    try:
        return judge.verdict(json.loads(path.read_text(encoding="utf-8")).get("expectations"), n) is not None
    except (json.JSONDecodeError, OSError):
        return False


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--parallel", type=int, default=4)
    ap.add_argument("--only", choices=["sweep", "continuity"])
    a = ap.parse_args()
    digest = image_digest()
    jobs = []
    if a.only in (None, "sweep"):
        jobs += list(sweep_jobs())
    if a.only in (None, "continuity"):
        jobs += list(continuity_jobs())
    todo = [j for j in jobs if not usable(j[0], len(j[2]))]
    judge.log(f"judge: {len(jobs)} passes planned, {len(jobs) - len(todo)} already done, {len(todo)} to go")

    def one(job):
        path, prompt, assertions, tag, info = job
        if STOP.is_set():
            return
        try:
            r = judge.run_judge(prompt, tag)
        except judge.JudgeUsageLimit as exc:
            STOP.set()
            judge.log(f"  USAGE LIMIT: {exc}. Stopping; finished passes are kept, rerun later.")
            return
        rec = {**info, "judge_model": judge.JUDGE_MODEL, "judge_effort": judge.JUDGE_EFFORT, "image": IMAGE,
               "image_digest": digest, "prompt_sha256_16": judge.prompt_sha(prompt), "assertions": assertions,
               "verdict": judge.verdict(r["expectations"], len(assertions)), **r}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(rec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="")
        judge.log(f"  {tag:45s} verdict={rec['verdict']} tools={r['tool_calls']} errors={len(r['errors'])}")

    with ThreadPoolExecutor(a.parallel) as ex:
        list(ex.map(one, todo))
    left = [j for j in jobs if not usable(j[0], len(j[2]))]
    judge.log(f"judge: done; {len(jobs) - len(left)}/{len(jobs)} passes usable")


if __name__ == "__main__":
    main()
