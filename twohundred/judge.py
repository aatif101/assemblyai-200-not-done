"""`looks_right`: an LLM reads the run's code against the task's written assertions (AssemblyAI's own eval method).

Fixed before grading (decision 35):
  * Judge: Codex CLI 0.169.0 in the runner image, model gpt-6.1-sol, reasoning effort medium, web search and
    every connector off, docs hosts blocked, read-only sandbox, no files mounted. Everything it sees is in the prompt.
  * Prompt: JUDGE_PROMPT below, the task's prompt.md, its rubric.json assertions, and the run's source files.
  * Each run is judged twice (pass 1 and pass 2), independently. `looks_right` is pass 1 with every assertion
    passed; pass 2 exists to report agreement.
The same judge re-grades the skill repo's committed eval outputs (continuity, Phase 3 plan).
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any

from .agentrun import BLOCKED_HOSTS, IMAGE, RUNNER
from .codexrun import AUTH_LOCK, SHARED_AUTH, _auth_secrets, config_toml, ensure_shared_auth
from .secrets import Scrubber, secret_values

JUDGE_MODEL = "gpt-6.1-sol"
JUDGE_EFFORT = "medium"
CODE_SUFFIXES = {".py", ".js", ".mjs", ".cjs", ".ts", ".sh", ".go", ".rb", ".java", ".rs"}
CODE_NAMES = {"requirements.txt", "package.json", "pyproject.toml", "run.sh"}
SKIP_PARTS = {"node_modules", ".venv", "venv", "site-packages", "__pycache__", "assemblyai", ".git"}
MAX_FILE_CHARS = 40_000
NAMED_IN_PROMPT = ("README.md",)  # decision 37

JUDGE_PROMPT = """You are grading code that a coding agent wrote for a user, the way AssemblyAI's skill evals are graded: \
read the code and decide whether it satisfies each written assertion. Do not run anything, do not use tools, and do not \
look anything up; judge only from the text below.

For each assertion, in the order given, return the assertion text unchanged, `passed` (true only if the code clearly \
satisfies it; false if it does not, or if you cannot tell from the code), and `evidence`: a short quote or file:line \
reference that supports your decision.

## What the user asked for

{prompt}

## Assertions

{assertions}

## The agent's code

{code}
"""

SCHEMA = {
    "type": "object",
    "properties": {"expectations": {"type": "array", "items": {
        "type": "object",
        "properties": {"text": {"type": "string"}, "passed": {"type": "boolean"}, "evidence": {"type": "string"}},
        "required": ["text", "passed", "evidence"], "additionalProperties": False}}},
    "required": ["expectations"], "additionalProperties": False,
}


class JudgeUsageLimit(RuntimeError):
    pass


def source_files(root: Path) -> list[Path]:
    out = []
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        if not p.is_file() or any(part in SKIP_PARTS for part in rel.parts[:-1]):
            continue
        if p.suffix in CODE_SUFFIXES or p.name in CODE_NAMES or p.name.startswith("requirements"):
            out.append(p)
    return out


def build_prompt(user_prompt: str, assertions: list[str], files: list[Path], root: Path) -> str:
    code = []
    for p in files:
        text = p.read_text(encoding="utf-8", errors="replace")
        if len(text) > MAX_FILE_CHARS:
            text = text[:MAX_FILE_CHARS] + "\n[... truncated ...]"
        numbered = "\n".join(f"{i:4d}  {line}" for i, line in enumerate(text.splitlines(), 1))
        code.append(f"### {p.relative_to(root).as_posix()}\n```\n{numbered}\n```")
    return JUDGE_PROMPT.format(prompt=user_prompt.strip(),
                               assertions="\n".join(f"{i}. {a}" for i, a in enumerate(assertions, 1)),
                               code="\n\n".join(code) or "(no source files)")


def prompt_sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def run_judge(prompt: str, tag: str) -> dict[str, Any]:
    """One judge call in a throwaway container. Returns parsed expectations plus usage and errors."""
    ensure_shared_auth()
    home = Path(tempfile.mkdtemp(prefix="judge-", dir=SHARED_AUTH.parent.parent))
    try:
        with AUTH_LOCK:
            shutil.copyfile(SHARED_AUTH, home / "auth.json")
        (home / "config.toml").write_text(config_toml(JUDGE_MODEL, JUDGE_EFFORT, None), encoding="utf-8", newline="\n")
        (home / "schema.json").write_text(json.dumps(SCHEMA), encoding="utf-8")
        scrub = Scrubber({**secret_values(dotenv=RUNNER.parent / ".env"), **_auth_secrets(home / "auth.json")})
        blocks = [f for h in BLOCKED_HOSTS for f in ("--add-host", f"{h}:127.0.0.1")]
        name = f"th-judge-{tag}".replace("_", "-").replace(".", "-").replace("+", "p").replace("/", "-")
        cmd = ["docker", "run", "--rm", "-i", "--name", name, *blocks, "-v", f"{home}:/home/agent/.codex", IMAGE,
               "codex", "exec", "--json", "--skip-git-repo-check", "--sandbox", "read-only", "-C", "/tmp",
               "--output-schema", "/home/agent/.codex/schema.json",
               "-o", "/home/agent/.codex/last.json", "-"]
        p = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=900)
        events = []
        for line in p.stdout.splitlines():
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                pass
        errors = [str(e.get("message") or e.get("error")) for e in events if e.get("type") in ("error", "turn.failed")]
        usage = next((e.get("usage") for e in events if e.get("type") == "turn.completed"), None)
        if any("usage limit" in e.lower() for e in errors):
            raise JudgeUsageLimit(errors[0][:200])
        last = home / "last.json"
        parsed = None
        if last.is_file():
            try:
                parsed = json.loads(last.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                parsed = None
        tools = sum(1 for e in events if e.get("type") == "item.completed"
                    and (e.get("item") or {}).get("type") in ("command_execution", "mcp_tool_call", "file_change"))
        return scrub.scrub_obj({"expectations": (parsed or {}).get("expectations"), "usage": usage,
                                "errors": errors[:3], "exit_code": p.returncode, "tool_calls": tools,
                                "stderr_tail": p.stderr[-500:] if p.returncode else ""})
    finally:
        with AUTH_LOCK:
            fresh = home / "auth.json"
            if fresh.is_file() and SHARED_AUTH.is_file() and fresh.read_bytes() != SHARED_AUTH.read_bytes():
                shutil.copyfile(fresh, SHARED_AUTH)
        shutil.rmtree(home, ignore_errors=True)


def verdict(expectations: list[dict] | None, n_assertions: int) -> bool | None:
    """True only if the judge returned one expectation per assertion and all passed. None if the output is unusable."""
    if not expectations or len(expectations) != n_assertions:
        return None
    return all(bool(e.get("passed")) for e in expectations)


_print_lock = threading.Lock()


def log(msg: str) -> None:
    with _print_lock:
        print(msg, flush=True)
