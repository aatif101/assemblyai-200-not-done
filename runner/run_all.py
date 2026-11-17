"""The 12-run pilot or the 120-run sweep. Resumable: finished runs are skipped.

    python runner/run_all.py pilot --model M [--budget-usd X] [--total-usd Y] [--parallel N] [--seed S]
    python runner/run_all.py sweep ...

Stops before starting a run once the reported spend over finished runs reaches --total-usd.
"""
import argparse
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from twohundred.agentrun import execute, is_complete, plan, run_dir, spent_so_far  # noqa: E402
from twohundred import codexrun  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent))
import mcp_relay  # noqa: E402
from twohundred.config import RESULTS, load_settings  # noqa: E402
from twohundred.rundir import write_scrubbed  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("kind", choices=["pilot", "sweep"])
ap.add_argument("--model", required=True)
ap.add_argument("--budget-usd", type=float, default=None, help="per-run cap passed to claude --max-budget-usd")
ap.add_argument("--total-usd", type=float, default=None, help="stop starting runs once finished runs report this much")
ap.add_argument("--parallel", type=int, default=1)
ap.add_argument("--seed", type=int, default=20261115)
ap.add_argument("--agent", choices=["claude", "codex"], default="claude")
ap.add_argument("--effort", default="medium", help="codex model_reasoning_effort")
a = ap.parse_args()

settings = load_settings()
relay = mcp_relay.start()
runs = plan(a.kind, a.seed)
root = RESULTS / "phase2" / ("codex" if a.agent == "codex" else "") / ("pilot" if a.kind == "pilot" else "runs")
rd = codexrun.run_dir if a.agent == "codex" else run_dir
write_scrubbed(root / "plan.json", {"kind": a.kind, "seed": a.seed, "model": a.model, "budget_usd": a.budget_usd,
                                    "total_usd": a.total_usd, "parallel": a.parallel, "agent": a.agent, "effort": a.effort,
                                    "order": [list(r) for r in runs]}, settings)
todo = [r for r in runs if not is_complete(rd(a.kind, *r))]
print(f"{a.kind}: {len(runs)} runs planned, {len(runs) - len(todo)} already done, {len(todo)} to go.")


STREAM_LOCK = threading.Lock()


def _exec(r):
    if a.agent == "codex":
        return codexrun.execute_codex(a.kind, *r, settings, a.model, a.effort)
    return execute(a.kind, *r, settings, a.model, a.budget_usd)


STOP = threading.Event()


def one(r):
    if STOP.is_set():
        return r, {"stopped": "usage limit reached earlier in this sweep"}
    if a.agent == "claude" and a.total_usd is not None and spent_so_far(a.kind) >= a.total_usd:
        return r, {"stopped": f"spend reached {a.total_usd}"}
    t0 = time.monotonic()
    try:
        return _one_timed(r, t0)
    except codexrun.UsageLimitHit as exc:
        STOP.set()
        print(f"  USAGE LIMIT: {exc}. Stopping; finished runs are kept, rerun the same command later.", flush=True)
        return r, {"stopped": "usage limit"}


def _one_timed(r, t0):
    if r[0] == "t1_stream_clinic":  # the account allows few concurrent streaming sessions (pilot v2)
        with STREAM_LOCK:
            meta = _exec(r)
    else:
        meta = _exec(r)
    ag = meta.get("agent") or {}
    print(f"  {r[0]:18s} {r[1]:9s} {r[2]:02d}  {round(time.monotonic() - t0)}s  turns={ag.get('num_turns')} "
          f"cost={ag.get('total_cost_usd')} cap={ag.get('cap_hit')} {meta.get('container_error') or ''}", flush=True)
    return r, meta


with ThreadPoolExecutor(max_workers=max(1, a.parallel)) as pool:
    results = list(pool.map(one, todo))
relay.shutdown()
print(f"docs MCP relay: {mcp_relay.Relay.counts}")
stopped = [r for r, m in results if m.get("stopped")]
print("done." if a.agent == "codex" else f"done. spent (reported, finished runs): ${spent_so_far(a.kind):.2f}"
      + (f"; {len(stopped)} runs not started because of --total-usd" if stopped else ""))
