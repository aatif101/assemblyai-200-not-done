# 200 ≠ done

**Do AI coding agents build AssemblyAI apps that actually work? I had one build 120 of them, then checked what AssemblyAI's servers really did, not what the code looked like.**

| Agent setup | Did what was asked | Used a current model |
|---|---|---|
| No help | 38 / 40 | 12 / 40 |
| With AssemblyAI's skill | 40 / 40 | 40 / 40 |
| Skill + docs server | 40 / 40 | 40 / 40 |

**What it means:** the skill works. Without it, agents learn model names from the Python SDK, which lists nine models, doesn't say which to use, and still includes a removed one. Six times the server quietly swapped the removed model for the new one. Twice the agent picked an older model, and nothing warned it.

**Three small fixes:** mark the recommended model in the SDK; have the server say so when it swaps a model; keep a test that runs the code ([proposed](PR_DRAFT.md)).

**See one example:** [the agent's code](results/phase2/codex/runs/t1_stream_clinic/bare/03/workspace/transcribe.py#L36) asks for `u3-rt-pro`; [the server's reply](results/phase2/codex/runs/t1_stream_clinic/bare/03/out.json.frames.jsonl) shows it ran `universal-3-6-pro`.

**How it was checked:** Codex CLI (`gpt-6.1-sol`) in Docker with AssemblyAI's docs blocked; 4 tasks × 3 setups × 10 runs. A run counts only if AssemblyAI's own record shows it did the job. The rules were written before the data, and the three changed afterwards are listed in the write-up.

**Go deeper:** [Write-up (600 words)](WRITEUP.md) · [Every run](results/phase2/codex/runs/) · Rebuild every number offline: `make grade` · [Re-run everything](docs/REPRODUCE.md) · Working on this repo with an agent? [AGENTS.md](AGENTS.md)
