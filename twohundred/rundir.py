"""Run-directory plumbing shared by the agent runner and `make oracle-check`.

Everything written here goes through the scrubber.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import requests

from .config import AUDIO, ENDPOINTS, RESULTS, ROOT, Settings
from .oracles import TASKS, grade
from .transport import _trim_transcript

ORACLE_CHECK = RESULTS / "phase2" / "oracle_check"


def write_scrubbed(path: Path, obj_or_text: Any, settings: Settings) -> None:
    text = obj_or_text if isinstance(obj_or_text, str) else json.dumps(obj_or_text, indent=2, ensure_ascii=False) + "\n"
    text = settings.scrubber.scrub(text)
    if settings.scrubber.contains_secret(text):
        raise RuntimeError(f"refusing to write {path.name}: a secret survived scrubbing")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="")  # keep line endings exactly as written


def task_spec(task: str) -> dict[str, Any]:
    return json.loads((TASKS / task / "task.json").read_text(encoding="utf-8"))


def fetch_server(run_dir: Path, settings: Settings) -> None:
    """GET the transcript the run reports and save it. Async tasks only."""
    try:
        out = json.loads((run_dir / "out.json").read_text(encoding="utf-8"))
        tid = out.get("transcript_id") if isinstance(out, dict) else None
    except (OSError, ValueError):
        tid = None
    if not isinstance(tid, str) or not tid.strip() or "/" in tid:
        write_scrubbed(run_dir / "server" / "fetch.json", {"fetched": False, "reason": "no usable transcript_id"}, settings)
        return
    r = requests.get(f"{ENDPOINTS['async']}/v2/transcript/{tid}", headers={"Authorization": settings.api_key}, timeout=60)
    meta = {"fetched": r.status_code == 200, "http_status": r.status_code, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    write_scrubbed(run_dir / "server" / "fetch.json", meta, settings)
    if r.status_code == 200:
        write_scrubbed(run_dir / "server" / "transcript.json", _trim_transcript(r.json()), settings)


def oracle_check(settings: Settings) -> int:
    """Live: run each task's reference solution in its right and wrong variant, grade both."""
    rows = []
    for task in sorted(p.name for p in TASKS.iterdir() if (p / "task.json").is_file()):
        spec = task_spec(task)
        for variant in ("good", "bad"):
            d = ORACLE_CHECK / task / variant
            d.mkdir(parents=True, exist_ok=True)
            t0 = time.monotonic()
            p = subprocess.run([sys.executable, str(ROOT / "runner" / "reference" / "solve.py"), task, variant,
                                str(AUDIO / spec["fixture"]), str(d / "out.json")],
                               capture_output=True, text=True, timeout=spec["contract_timeout_s"],
                               env={**os.environ, "ASSEMBLYAI_API_KEY": settings.api_key})
            write_scrubbed(d / "contract.json", {"exit_code": p.returncode, "timed_out": False,
                                                 "seconds": round(time.monotonic() - t0, 1),
                                                 "stderr_tail": p.stderr[-2000:]}, settings)
            for name in ("out.json", "out.json.frames.jsonl"):  # rewrite through the scrubber
                f = d / name
                if f.is_file():
                    write_scrubbed(f, f.read_text(encoding="utf-8"), settings)
            if spec["surface"] != "streaming":
                fetch_server(d, settings)
            g = grade(task, d)
            write_scrubbed(d / "grade.json", g, settings)
            expect = variant == "good"
            ok = g["runs"] and g["outcome"] == expect
            failed = [k for k, c in g["checks"].items() if not c["pass"]]
            rows.append({"task": task, "variant": variant, "runs": g["runs"], "outcome": g["outcome"],
                         "as_expected": ok, "failed_checks": failed})
            print(f"  {task:18s} {variant:4s} runs={g['runs']!s:5s} outcome={g['outcome']!s:5s} "
                  f"{'OK' if ok else 'UNEXPECTED'}  failed: {', '.join(failed) or '-'}")
            if spec["surface"] == "streaming":
                time.sleep(20)  # concurrent-session limit seen in Phase 1
    write_scrubbed(ORACLE_CHECK / "summary.json", rows, settings)
    bad = [r for r in rows if not r["as_expected"]]
    print(f"\n{len(rows) - len(bad)} of {len(rows)} as expected.")
    return 1 if bad else 0
