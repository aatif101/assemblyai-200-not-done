"""Phase 2 agent runs: one (task, condition, index) per call, headless Claude Code in Docker.

Flow for one run (runner/run_one.py, runner/run_all.py):
  1. Build a scratch workspace: the task's seed (T4), sample.wav, and for the skill conditions
     the skill at SKILL_COMMIT under .claude/skills/assemblyai.
  2. Start a container from the runner image. Keys enter as runtime environment variables
     only (`docker run -e NAME`, value inherited, never on a command line or in a file).
  3. Pipe the task prompt to `claude -p` inside it; stamp every stream-json line with the
     host receive time; scrub; save as agent.jsonl. Caps: --max-turns, wall clock, optional
     --max-budget-usd.
  4. The harness, not the agent, runs `./run.sh /fixtures/<clip> /out/out.json` in a fresh
     process in the same container, with a timeout.
  5. Copy the workspace out (no audio, dependencies or the skill), GET the transcript the run
     reports, write meta.json last. A run with a complete meta.json is skipped on resume.
"""
from __future__ import annotations

import json
import os
import random
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from .config import AUDIO, RESULTS, ROOT, SKILL_COMMIT, Settings
from .oracles import TASKS
from .rundir import fetch_server, task_spec, write_scrubbed

RUNNER = ROOT / "runner"
WORK = RUNNER / ".work"
CONDITIONS = ["bare", "skill", "skill+mcp"]
TASK_IDS = ["t1_stream_clinic", "t2_async_redact", "t3_british", "t4_legacy_upgrade"]
IMAGE = "twohundred-runner"

MAX_TURNS = 40          # decision 23
MAX_AGENT_SECONDS = 20 * 60
DISALLOWED_TOOLS = ["WebSearch", "WebFetch"]  # decision 18
SKIP_DIRS = {".venv", "venv", "env", "node_modules", "__pycache__", ".git", ".mypy_cache", ".pytest_cache",
             "site-packages", ".cache", ".npm"}
SKIP_SUFFIXES = {".wav", ".mp3", ".flac", ".ogg", ".m4a", ".webm", ".pyc", ".so", ".whl", ".zip", ".tar", ".gz"}
MAX_COPY_BYTES = 512 * 1024
ANTHROPIC_CRED_NAMES = ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN")
# Documentation hosts unreachable from every container (decision 30). Turning off WebSearch and
# WebFetch was not enough: in pilot v1 a bare agent read the docs website with curl. The docs MCP
# server shares www.assemblyai.com, so skill+mcp reaches it through runner/mcp_relay.py instead.
BLOCKED_HOSTS = ["www.assemblyai.com", "assemblyai.com", "github.com", "www.github.com", "raw.githubusercontent.com",
                 "api.github.com", "codeload.github.com", "objects.githubusercontent.com", "gist.github.com",
                 "gist.githubusercontent.com"]


# ---------------------------------------------------------------- planning (pure)


def plan(kind: str, seed: int) -> list[tuple[str, str, int]]:
    """pilot: one run per cell (12). sweep: ten per cell (120). Order shuffled with a recorded seed."""
    n = 1 if kind == "pilot" else 10
    runs = [(t, c, i) for t in TASK_IDS for c in CONDITIONS for i in range(n)]
    random.Random(seed).shuffle(runs)
    return runs


def run_dir(kind: str, task: str, cond: str, idx: int) -> Path:
    return RESULTS / "phase2" / ("pilot" if kind == "pilot" else "runs") / task / cond / f"{idx:02d}"


def is_complete(d: Path) -> bool:
    try:
        return json.loads((d / "meta.json").read_text(encoding="utf-8")).get("complete") is True
    except (OSError, ValueError):
        return False


def summarize_stream(lines: list[dict]) -> dict[str, Any]:
    """Read the terminal `result` event of a stream-json log."""
    result = next((l for l in reversed(lines) if l.get("type") == "result"), None)
    init = next((l for l in lines if l.get("type") == "system" and l.get("subtype") == "init"), {})
    out: dict[str, Any] = {"result_event": result is not None, "model_reported": init.get("model"),
                           "tools": init.get("tools"), "mcp_servers": init.get("mcp_servers"),
                           "skills": init.get("skills")}
    if result:
        out.update(subtype=result.get("subtype"), is_error=result.get("is_error"),
                   num_turns=result.get("num_turns"), total_cost_usd=result.get("total_cost_usd"),
                   duration_ms=result.get("duration_ms"), usage=result.get("usage"))
    sub = str(out.get("subtype") or "")
    out["cap_hit"] = ("turns" if "max_turns" in sub else "budget" if "budget" in sub else None)
    return out


def should_copy(rel: Path, size: int) -> bool:
    parts = rel.parts
    if any(p in SKIP_DIRS for p in parts):
        return False
    if len(parts) >= 2 and parts[0] == ".claude" and parts[1] == "skills":
        return False
    if rel.suffix.lower() in SKIP_SUFFIXES or size > MAX_COPY_BYTES:
        return False
    return True


def spent_so_far(kind: str) -> float:
    """Sum of reported cost over every finished run of this kind (and the pilot, for a sweep)."""
    total = 0.0
    roots = [RESULTS / "phase2" / "pilot"] + ([RESULTS / "phase2" / "runs"] if kind != "pilot" else [])
    for root in roots:
        for meta in root.glob("*/*/*/meta.json"):
            try:
                total += float(json.loads(meta.read_text(encoding="utf-8")).get("agent", {}).get("total_cost_usd") or 0)
            except (OSError, ValueError, TypeError):
                pass
    return total


# ---------------------------------------------------------------- workspace


def _copy_lf(src: Path, dst: Path) -> None:
    """Seed files go in with LF endings whatever the host checkout did (a CRLF run.sh will not start)."""
    for p in src.rglob("*"):
        if p.is_dir():
            continue
        target = dst / p.relative_to(src)
        target.parent.mkdir(parents=True, exist_ok=True)
        data = p.read_bytes()
        if b"\0" not in data:
            data = data.replace(b"\r\n", b"\n")
        target.write_bytes(data)
        if p.suffix == ".sh":
            target.chmod(0o755)


def skill_repo() -> Path:
    for cand in (os.environ.get("SKILL_REPO"), str(ROOT.parent / "assemblyai-skill")):
        if cand and (Path(cand) / "skills" / "assemblyai" / "SKILL.md").is_file():
            return Path(cand)
    raise SystemExit("assemblyai-skill checkout not found next to this repo (or set SKILL_REPO).")


def skill_commit(repo: Path) -> str:
    return subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()


def build_workspace(task: str, cond: dict, ws: Path) -> None:
    if ws.exists():
        shutil.rmtree(ws)
    ws.mkdir(parents=True)
    spec = task_spec(task)
    seed = TASKS / task / "seed"
    if seed.is_dir():
        _copy_lf(seed, ws)
    shutil.copyfile(AUDIO / spec["fixture"], ws / "sample.wav")
    if cond["skill"]:
        repo = skill_repo()
        got = skill_commit(repo)
        if got != SKILL_COMMIT:
            raise SystemExit(f"skill checkout is at {got[:12]}, expected {SKILL_COMMIT[:12]}")
        shutil.copytree(repo / "skills" / "assemblyai", ws / ".claude" / "skills" / "assemblyai")


def collect_workspace(ws: Path, dst: Path, settings: Settings) -> list[str]:
    skipped = []
    for p in sorted(ws.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(ws)
        if not should_copy(rel, p.stat().st_size):
            skipped.append(rel.as_posix())
            continue
        data = p.read_bytes()
        if b"\0" in data[:4096]:
            skipped.append(rel.as_posix())
            continue
        write_scrubbed(dst / rel, data.decode("utf-8", "replace"), settings)
    return skipped


# ---------------------------------------------------------------- docker


def docker(*args: str, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, **kw)


def image_digest() -> str:
    p = docker("image", "inspect", IMAGE, "--format", "{{.Id}}")
    return p.stdout.strip() if p.returncode == 0 else ""


def claude_args(model: str, budget_usd: float | None) -> list[str]:
    args = ["claude", "-p", "--output-format", "stream-json", "--verbose", "--model", model,
            "--max-turns", str(MAX_TURNS), "--dangerously-skip-permissions",
            "--disallowedTools", *DISALLOWED_TOOLS,
            "--strict-mcp-config", "--mcp-config", "/cfg/mcp.json", "--no-session-persistence"]
    if budget_usd:
        args += ["--max-budget-usd", f"{budget_usd:.2f}"]
    return args


def _env_for_docker(settings: Settings, creds: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    env = {**os.environ, "ASSEMBLYAI_API_KEY": settings.api_key, **creds}
    flags = ["-e", "ASSEMBLYAI_API_KEY"]
    for name in creds:
        flags += ["-e", name]
    return env, flags


def anthropic_creds() -> dict[str, str]:
    from .secrets import load_dotenv
    env = {**load_dotenv(ROOT / ".env"), **os.environ}
    return {n: env[n] for n in ANTHROPIC_CRED_NAMES if env.get(n)}


def execute(kind: str, task: str, cond_id: str, idx: int, settings: Settings, model: str,
            budget_usd: float | None) -> dict[str, Any]:
    d = run_dir(kind, task, cond_id, idx)
    if is_complete(d):
        return {"skipped": True, "dir": str(d)}
    if d.exists():
        shutil.rmtree(d)  # an unfinished earlier attempt: start clean, and say so in meta
    d.mkdir(parents=True)
    spec = task_spec(task)
    cond = json.loads((RUNNER / "conditions" / f"{cond_id}.json").read_text(encoding="utf-8"))
    tag = f"{task}.{cond_id.replace('+', '_')}.{idx:02d}"
    ws, cfg, out = WORK / tag / "work", WORK / tag / "cfg", WORK / tag / "out"
    build_workspace(task, cond, ws)
    for p in (cfg, out):
        shutil.rmtree(p, ignore_errors=True)
        p.mkdir(parents=True)
    (cfg / "mcp.json").write_text(json.dumps(cond["mcp"]), encoding="utf-8")

    creds = anthropic_creds()
    if not creds:
        raise SystemExit("No Anthropic credential: set ANTHROPIC_API_KEY or CLAUDE_CODE_OAUTH_TOKEN (shell or .env).")
    env, env_flags = _env_for_docker(settings, creds)
    name = f"th-{tag}".replace("_", "-").replace(".", "-")
    meta: dict[str, Any] = {"task": task, "condition": cond_id, "index": idx, "kind": kind, "model": model,
                            "max_turns": MAX_TURNS, "max_agent_seconds": MAX_AGENT_SECONDS,
                            "max_budget_usd": budget_usd, "skill_commit": SKILL_COMMIT if cond["skill"] else None,
                            "mcp": cond["mcp"], "disallowed_tools": DISALLOWED_TOOLS, "blocked_hosts": BLOCKED_HOSTS,
                            "credential_kind": sorted(creds), "image": IMAGE, "image_digest": image_digest(),
                            "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    docker("rm", "-f", name)
    blocks = [f for h in BLOCKED_HOSTS for f in ("--add-host", f"{h}:127.0.0.1")]
    started = docker("run", "-d", "--name", name, *env_flags, *blocks,
                     "-v", f"{ws}:/work", "-v", f"{cfg}:/cfg:ro", "-v", f"{out}:/out",
                     "-v", f"{AUDIO}:/fixtures:ro", IMAGE, env=env)
    if started.returncode != 0:
        meta.update(container_error=settings.scrubber.scrub(started.stderr[-1000:]), complete=True)
        write_scrubbed(d / "contract.json", {"exit_code": None, "container_error": True}, settings)
        write_scrubbed(d / "meta.json", meta, settings)
        return meta
    try:
        meta["claude_version"] = docker("exec", name, "claude", "--version").stdout.strip()
        # ---- the agent
        prompt = (TASKS / task / "prompt.md").read_text(encoding="utf-8")
        lines: list[dict] = []
        log = (d / "agent.jsonl").open("w", encoding="utf-8")
        t0 = time.monotonic()
        proc = subprocess.Popen(["docker", "exec", "-i", name, *claude_args(model, budget_usd)],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, encoding="utf-8", errors="replace")
        timed_out = threading.Event()

        def _kill() -> None:
            timed_out.set()
            docker("exec", name, "pkill", "-f", "claude")
            proc.kill()

        timer = threading.Timer(MAX_AGENT_SECONDS, _kill)
        timer.start()
        proc.stdin.write(prompt)
        proc.stdin.close()
        for raw in proc.stdout:
            raw = raw.strip()
            if not raw:
                continue
            try:
                msg = json.loads(raw)
            except ValueError:
                msg = {"_unparsed": raw[:4000]}
            lines.append(msg)
            stamped = {"t": round(time.monotonic() - t0, 3), "wall": time.time(), "msg": msg}
            text = settings.scrubber.scrub(json.dumps(stamped, ensure_ascii=False))
            log.write(text + "\n")
        proc.wait()
        timer.cancel()
        log.close()
        stderr = proc.stderr.read()
        agent = summarize_stream(lines)
        if timed_out.is_set():
            agent["cap_hit"] = "time"
        agent.update(exit_code=proc.returncode, seconds=round(time.monotonic() - t0, 1),
                     stderr_tail=settings.scrubber.scrub(stderr[-2000:]))
        meta["agent"] = agent
        # ---- the harness runs the contract
        clip = spec["fixture"]
        c0 = time.monotonic()
        cmd = (f"cd /work && chmod +x run.sh 2>/dev/null; "
               f"timeout {spec['contract_timeout_s']} ./run.sh /fixtures/{clip} /out/out.json")
        c = docker("exec", name, "sh", "-c", cmd, encoding="utf-8", errors="replace")
        contract = {"exit_code": c.returncode, "timed_out": c.returncode == 124,
                    "seconds": round(time.monotonic() - c0, 1), "cap_hit": agent.get("cap_hit"),
                    "stdout_tail": c.stdout[-4000:], "stderr_tail": c.stderr[-4000:],
                    "files": sorted(p.name for p in out.iterdir())}
        write_scrubbed(d / "contract.json", contract, settings)
    finally:
        docker("rm", "-f", name)
    for f in ("out.json", "out.json.frames.jsonl"):
        if (out / f).is_file():
            write_scrubbed(d / f, (out / f).read_text(encoding="utf-8", errors="replace"), settings)
    meta["workspace_skipped"] = collect_workspace(ws, d / "workspace", settings)
    if spec["surface"] != "streaming":
        fetch_server(d, settings)
    meta.update(ended_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), complete=True)
    write_scrubbed(d / "meta.json", meta, settings)
    shutil.rmtree(WORK / tag, ignore_errors=True)
    return meta
