"""One agent run:  python runner/run_one.py <task> <condition> <index> [--pilot] --model M [--budget-usd X]"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from twohundred.agentrun import CONDITIONS, TASK_IDS, execute  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent))
import mcp_relay  # noqa: E402
from twohundred.config import load_settings  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("task", choices=TASK_IDS)
ap.add_argument("condition", choices=CONDITIONS)
ap.add_argument("index", type=int)
ap.add_argument("--pilot", action="store_true")
ap.add_argument("--model", required=True)
ap.add_argument("--budget-usd", type=float, default=None)
a = ap.parse_args()
relay = mcp_relay.start()
meta = execute("pilot" if a.pilot else "sweep", a.task, a.condition, a.index, load_settings(), a.model, a.budget_usd)
print(json.dumps({k: meta.get(k) for k in ("skipped", "task", "condition", "index", "agent", "container_error")},
                 indent=2, default=str)[:3000])
