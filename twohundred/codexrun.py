"""Phase 2 agent runs with Codex CLI (decision 32), on the owner's ChatGPT subscription.

Same tasks, prompts, fixtures, caps, docs block, MCP relay, contract run and oracles as the
Claude Code path in agentrun.py. What differs:

- `codex exec --json` instead of `claude -p`; the prompt is piped on stdin.
- Each run gets its own CODEX_HOME (config.toml + a copy of the owner's auth.json). The owner's
  ~/.codex is never mounted, so their global instructions, sessions and connectors stay out.
  A shared working copy of auth.json lives under runner/.work/codex_auth/ and is updated if
  Codex refreshes the token; ~/.codex/auth.json itself is never written.
- Web search off, and every feature that reaches outside the container or the owner's account
  is off: connected apps, plugins, browser use, computer use, image generation.
- skill: AssemblyAI's documented Codex install (assemblyai-skill README): the skill folder copied
  into the project and AGENTS.md pointing at it, verbatim.
- skill+mcp: the docs MCP server in config.toml, through the same /docs/mcp-only relay.
- Turn cap: Codex has no --max-turns. A "turn" here is one completed tool item (command, file
  change, MCP call); at MAX_TURNS + 1 the run is stopped and marked cap_hit="turns".
"""
from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from .agentrun import (AUDIO, BLOCKED_HOSTS, IMAGE, MAX_AGENT_SECONDS, MAX_TURNS, RUNNER, SKILL_COMMIT, WORK,
                       _copy_lf, collect_workspace, docker, image_digest, is_complete, skill_commit, skill_repo)
from .config import RESULTS, Settings
from .oracles import TASKS
from .rundir import fetch_server, task_spec, write_scrubbed
from .secrets import Scrubber, secret_values

CODEX_HOME_HOST = Path.home() / ".codex"
SHARED_AUTH = WORK / "codex_auth" / "auth.json"
AUTH_LOCK = threading.Lock()
TOOL_ITEMS = {"command_execution", "file_change", "mcp_tool_call", "web_search", "collab_tool_call"}
AGENTS_MD_LINE = "When working with AssemblyAI, read and follow the instructions in assemblyai/SKILL.md"
DISABLED_FEATURES = ["apps", "plugins", "remote_plugin", "browser_use", "browser_use_external", "computer_use",
                     "image_generation"]
DEFAULT_EFFORT = "medium"


class UsageLimitHit(RuntimeError):
    """The owner's plan ran out. Not an agent outcome: the run is discarded and the sweep stops (decision 34)."""


def run_dir(kind: str, task: str, cond: str, idx: int) -> Path:
    return RESULTS / "phase2" / "codex" / ("pilot" if kind == "pilot" else "runs") / task / cond / f"{idx:02d}"


def config_toml(model: str, effort: str, mcp_url: str | None) -> str:
    lines = [f'model = "{model}"', f'model_reasoning_effort = "{effort}"', 'approval_policy = "never"',
             'sandbox_mode = "danger-full-access"', 'web_search = "disabled"', "", "[features]"]
    lines += [f"{f} = false" for f in DISABLED_FEATURES]
    if mcp_url:
        lines += ["", "[mcp_servers.assemblyai-docs]", f'url = "{mcp_url}"']
    return "\n".join(lines) + "\n"


def summarize_events(lines: list[dict]) -> dict[str, Any]:
    tools = [l["item"] for l in lines if l.get("type") == "item.completed"
             and isinstance(l.get("item"), dict) and l["item"].get("type") in TOOL_ITEMS]
    usage: dict[str, int] = {}
    for l in lines:
        if l.get("type") == "turn.completed":
            for k, v in (l.get("usage") or {}).items():
                if isinstance(v, int):
                    usage[k] = usage.get(k, 0) + v
    failed = any(l.get("type") in ("turn.failed", "error") for l in lines)
    completed = any(l.get("type") == "turn.completed" for l in lines)
    cmds = [str(t.get("command") or "") for t in tools if t.get("type") == "command_execution"]
    return {"result_event": completed or failed, "subtype": "success" if completed and not failed else "error",
            "num_turns": len(tools), "usage": usage, "total_cost_usd": None,
            "mcp_calls": sum(1 for t in tools if t.get("type") == "mcp_tool_call"),
            "skill_read": any("SKILL.md" in c or "assemblyai/references" in c for c in cmds),
            "errors": [str(l.get("message") or l.get("error"))[:300] for l in lines
                       if l.get("type") in ("turn.failed", "error")][:3]}


def _auth_secrets(path: Path) -> dict[str, str]:
    """Every long string in auth.json, so the scrubber removes tokens wherever they appear."""
    out: dict[str, str] = {}

    def walk(o: Any, name: str) -> None:
        if isinstance(o, dict):
            for k, v in o.items():
                walk(v, f"{name}.{k}")
        elif isinstance(o, str) and len(o) >= 20:
            out[f"CODEX_AUTH{name}"] = o

    try:
        walk(json.loads(path.read_text(encoding="utf-8")), "")
    except (OSError, ValueError):
        pass
    return out


def ensure_shared_auth() -> None:
    if not SHARED_AUTH.is_file():
        src = CODEX_HOME_HOST / "auth.json"
        if not src.is_file():
            raise SystemExit("No ~/.codex/auth.json: sign in to Codex first (codex login).")
        SHARED_AUTH.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, SHARED_AUTH)


def execute_codex(kind: str, task: str, cond_id: str, idx: int, settings: Settings, model: str,
                  effort: str = DEFAULT_EFFORT) -> dict[str, Any]:
    d = run_dir(kind, task, cond_id, idx)
    if is_complete(d):
        return {"skipped": True, "dir": str(d)}
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    spec = task_spec(task)
    cond = json.loads((RUNNER / "conditions" / f"{cond_id}.json").read_text(encoding="utf-8"))
    tag = f"codex.{task}.{cond_id.replace('+', '_')}.{idx:02d}"
    base = WORK / tag
    ws, home, out = base / "work", base / "codexhome", base / "out"
    shutil.rmtree(base, ignore_errors=True)
    ws.mkdir(parents=True)
    home.mkdir()
    out.mkdir()

    # workspace
    seed = TASKS / task / "seed"
    if seed.is_dir():
        _copy_lf(seed, ws)
    shutil.copyfile(AUDIO / spec["fixture"], ws / "sample.wav")
    skill_files: set[str] = set()
    if cond["skill"]:
        repo = skill_repo()
        got = skill_commit(repo)
        if got != SKILL_COMMIT:
            raise SystemExit(f"skill checkout is at {got[:12]}, expected {SKILL_COMMIT[:12]}")
        shutil.copytree(repo / "skills" / "assemblyai", ws / "assemblyai")
        (ws / "AGENTS.md").write_text(AGENTS_MD_LINE + "\n", encoding="utf-8", newline="\n")
        skill_files = {p.relative_to(ws).as_posix() for p in (ws / "assemblyai").rglob("*") if p.is_file()}

    # codex home
    ensure_shared_auth()
    with AUTH_LOCK:
        shutil.copyfile(SHARED_AUTH, home / "auth.json")
    mcp_url = cond["mcp"]["mcpServers"].get("assemblyai-docs", {}).get("url") if cond["mcp"]["mcpServers"] else None
    (home / "config.toml").write_text(config_toml(model, effort, mcp_url), encoding="utf-8", newline="\n")
    scrub = Scrubber({**secret_values(dotenv=RUNNER.parent / ".env"), "ASSEMBLYAI_API_KEY": settings.api_key,
                      **_auth_secrets(home / "auth.json")})
    s2 = Settings(api_key=settings.api_key, scrubber=scrub)

    name = f"th-{tag}".replace("_", "-").replace(".", "-")
    meta: dict[str, Any] = {"agent": None, "agent_cli": "codex", "task": task, "condition": cond_id, "index": idx,
                            "kind": kind, "model": model, "reasoning_effort": effort, "max_turns": MAX_TURNS,
                            "turn_rule": "completed tool items (command, file change, MCP call)",
                            "max_agent_seconds": MAX_AGENT_SECONDS, "skill_commit": SKILL_COMMIT if cond["skill"] else None,
                            "skill_install": "assemblyai/ + AGENTS.md pointer (assemblyai-skill README, Codex)" if cond["skill"] else None,
                            "mcp_url": mcp_url, "disabled_features": DISABLED_FEATURES, "web_search": "disabled",
                            "blocked_hosts": BLOCKED_HOSTS, "credential_kind": ["codex auth.json (ChatGPT plan)"],
                            "image": IMAGE, "image_digest": image_digest(),
                            "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    docker("rm", "-f", name)
    blocks = [f for h in BLOCKED_HOSTS for f in ("--add-host", f"{h}:127.0.0.1")]
    env = {**__import__("os").environ, "ASSEMBLYAI_API_KEY": settings.api_key}
    started = docker("run", "-d", "--name", name, "-e", "ASSEMBLYAI_API_KEY", *blocks,
                     "-v", f"{ws}:/work", "-v", f"{home}:/home/agent/.codex", "-v", f"{out}:/out",
                     "-v", f"{AUDIO}:/fixtures:ro", IMAGE, env=env)
    if started.returncode != 0:
        meta.update(container_error=s2.scrubber.scrub(started.stderr[-1000:]), complete=True)
        write_scrubbed(d / "contract.json", {"exit_code": None, "container_error": True}, s2)
        write_scrubbed(d / "meta.json", meta, s2)
        return meta
    try:
        meta["codex_version"] = docker("exec", name, "codex", "--version").stdout.strip()
        prompt = (TASKS / task / "prompt.md").read_text(encoding="utf-8")
        lines: list[dict] = []
        log = (d / "agent.jsonl").open("w", encoding="utf-8", newline="")
        t0 = time.monotonic()
        proc = subprocess.Popen(["docker", "exec", "-i", name, "codex", "exec", "--json", "--skip-git-repo-check",
                                 "--dangerously-bypass-approvals-and-sandbox", "-"],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, encoding="utf-8", errors="replace")
        cap: dict[str, str] = {}

        def _kill(reason: str) -> None:
            cap.setdefault("hit", reason)
            docker("exec", name, "pkill", "-f", "codex")
            proc.kill()

        timer = threading.Timer(MAX_AGENT_SECONDS, _kill, args=("time",))
        timer.start()
        proc.stdin.write(prompt)
        proc.stdin.close()
        tools = 0
        for raw in proc.stdout:
            raw = raw.strip()
            if not raw:
                continue
            try:
                msg = json.loads(raw)
            except ValueError:
                msg = {"_unparsed": raw[:4000]}
            lines.append(msg)
            log.write(s2.scrubber.scrub(json.dumps({"t": round(time.monotonic() - t0, 3), "wall": time.time(),
                                                    "msg": msg}, ensure_ascii=False)) + "\n")
            if msg.get("type") == "item.completed" and (msg.get("item") or {}).get("type") in TOOL_ITEMS:
                tools += 1
                if tools > MAX_TURNS:
                    _kill("turns")
        proc.wait()
        timer.cancel()
        log.close()
        stderr = proc.stderr.read()
        agent = summarize_events(lines)
        agent.update(cap_hit=cap.get("hit"), exit_code=proc.returncode, seconds=round(time.monotonic() - t0, 1),
                     stderr_tail=s2.scrubber.scrub(stderr[-2000:]))
        meta["agent"] = agent
        if any("usage limit" in e.lower() for e in agent.get("errors") or []):
            limit_msg = next(e for e in agent["errors"] if "usage limit" in e.lower())
            raise UsageLimitHit(limit_msg[limit_msg.lower().find("try again"):][:60] or "usage limit")
        clip = spec["fixture"]
        c0 = time.monotonic()
        cmd = (f"cd /work && chmod +x run.sh 2>/dev/null; "
               f"timeout {spec['contract_timeout_s']} ./run.sh /fixtures/{clip} /out/out.json")
        c = docker("exec", name, "sh", "-c", cmd, encoding="utf-8", errors="replace")
        write_scrubbed(d / "contract.json", {"exit_code": c.returncode, "timed_out": c.returncode == 124,
                                             "seconds": round(time.monotonic() - c0, 1), "cap_hit": agent.get("cap_hit"),
                                             "stdout_tail": c.stdout[-4000:], "stderr_tail": c.stderr[-4000:],
                                             "files": sorted(p.name for p in out.iterdir())}, s2)
    except UsageLimitHit:
        docker("rm", "-f", name)
        shutil.rmtree(d, ignore_errors=True)  # no partial run left behind; it reruns on resume
        shutil.rmtree(base, ignore_errors=True)
        raise
    finally:
        docker("rm", "-f", name)
        with AUTH_LOCK:  # keep a refreshed token for the next run; never touch ~/.codex
            fresh = home / "auth.json"
            if fresh.is_file() and SHARED_AUTH.is_file() and fresh.read_bytes() != SHARED_AUTH.read_bytes():
                shutil.copyfile(fresh, SHARED_AUTH)
                meta["auth_refreshed"] = True
    for f in ("out.json", "out.json.frames.jsonl"):
        if (out / f).is_file():
            write_scrubbed(d / f, (out / f).read_text(encoding="utf-8", errors="replace"), s2)
    for rel in skill_files:  # the harness put these there; do not copy them back as the agent's work
        (ws / rel).unlink(missing_ok=True)
    meta["workspace_skipped"] = collect_workspace(ws, d / "workspace", s2)
    if spec["surface"] != "streaming":
        fetch_server(d, s2)
    meta.update(ended_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), complete=True)
    write_scrubbed(d / "meta.json", meta, s2)
    shutil.rmtree(base, ignore_errors=True)
    return meta
