"""Time to first working call (Phase 2 Q5), applied mechanically to a Codex event log.

Q5: "the first tool result in which the agent's own execution of its code exits 0 after a request to an AssemblyAI
host; record seconds and tokens up to that event, and null if it never happens."

How that reads on Codex events (decision 36). A `command_execution` item counts when:
  1. it exited 0;
  2. it executes code (python, node, a shell script, curl to an API host), and is not a package install,
     a plain file read, or a test runner (unit tests are mocked and make no request);
  3. either it runs the agent's own app on the sample audio (`./run.sh sample.wav ...`, `python x.py sample.wav`):
     the task contract says exit 0 means success; or its output, or the output of the very next command if that
     command only reads a file, carries evidence that an AssemblyAI API answered: a streaming `Begin` with a session
     id, a transcript-style UUID, or a transcript status of completed/queued/processing, and no transcript
     `"status": "error"`. A command that runs the app counts even if it also runs the unit tests.
Validated by hand on the 12 pilot runs (decision 36).
Seconds are the event's `t` (seconds since the agent started). Codex reports token usage only once, at the end of
the turn, so "tokens up to that event" is not available; the write-up reports total tokens per run instead.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
BEGIN = re.compile(r"""["']?type["']?\s*[:=]\s*["']Begin["'].{0,40}?["']?id["']?\s*[:=]\s*["'][0-9a-f]{8}-""", re.S)
STATUS_OK = re.compile(r"""["']?status["']?\W{1,6}(?:completed|queued|processing)\b""", re.I)
ERROR_STATUS = re.compile(r"""["']?status["']?\s*[:=]\s*["']?error\b""", re.I)
EXECUTES = re.compile(r"\bpython3?\b|\bnode\b|\bnpx\b|\btsx\b|\./[\w.-]+\.sh\b|\b(?:ba)?sh\s+[\w./-]+\.sh\b|"
                      r"\bcurl\b[^\n]*\b(?:api|streaming)\.assemblyai\.com")
NOT_A_CALL = re.compile(r"\b(?:pip3?|python3? -m pip|npm|pnpm|yarn)\s+(?:install|index|download|show)\b|"
                        r"\bunittest\b|\bpytest\b|\bjest\b|node --test|\bpy_compile\b|\bsh -n\b")
RUNS_APP = re.compile(r"(?:\./|\b(?:ba)?sh\s+|\bpython3?\s+|\bnode\s+)[\w./-]+\s+[^|;&\n]*?\bsample\.(?:wav|mp3)\b")
READ_ONLY = re.compile(r"^\s*(?:/bin/bash -lc\s+)?['\"]?\s*(?:cat|jq|head|tail|less|sed -n|python3? -m json\.tool)\b")


def _commands(agent_jsonl: Path) -> list[dict[str, Any]]:
    out = []
    for line in agent_jsonl.read_text(encoding="utf-8").splitlines():
        o = json.loads(line)
        m = o.get("msg") or {}
        it = m.get("item") or {}
        if m.get("type") == "item.completed" and it.get("type") == "command_execution":
            out.append({"t": o.get("t"), "command": str(it.get("command") or ""), "exit_code": it.get("exit_code"),
                        "output": str(it.get("aggregated_output") or "")})
    return out


def answered(output: str) -> bool:
    if ERROR_STATUS.search(output):
        return False
    return bool(BEGIN.search(output) or UUID.search(output) or STATUS_OK.search(output))


def first_working_call(agent_jsonl: Path) -> dict[str, Any]:
    cmds = _commands(agent_jsonl)
    for i, c in enumerate(cmds):
        app_run = bool(RUNS_APP.search(c["command"]))
        if c["exit_code"] != 0 or not EXECUTES.search(c["command"]) or (NOT_A_CALL.search(c["command"]) and not app_run):
            continue
        evidence = c["output"]
        nxt = cmds[i + 1] if i + 1 < len(cmds) else None
        via_next = False
        if not answered(evidence) and nxt and nxt["exit_code"] == 0 and READ_ONLY.search(nxt["command"]):
            evidence, via_next = nxt["output"], True
        if app_run or answered(evidence):
            return {"seconds": c["t"], "command_index": i, "evidence_from_next_read": via_next and not app_run,
                    "app_run": app_run, "command": c["command"][:300]}
    return {"seconds": None, "command_index": None, "evidence_from_next_read": False, "app_run": False, "command": None}


def total_tokens(meta: dict[str, Any]) -> dict[str, int | None]:
    u = ((meta.get("agent") or {}).get("usage")) or {}
    inp, out = u.get("input_tokens"), u.get("output_tokens")
    return {"input_tokens": inp, "cached_input_tokens": u.get("cached_input_tokens"), "output_tokens": out,
            "total_tokens": (inp or 0) + (out or 0) if inp is not None or out is not None else None}
