# Archived runs

`main` keeps only the runs behind the published numbers:

- `phase2/codex/runs/`: the 120-run sweep.
- `phase2/codex/pilot/`: its 12-run pilot. The first-call rule was checked by hand on it.
- `phase2/precheck/` and `phase2/oracle_check/`: the pre-registered feasibility checks and the reference check.
- `phase3/`: everything graded.

Everything else is on the branch `archive/all-runs-2026-11-16`, unchanged and with the same scrubbing. It holds:

| Path on the archive branch | What it is | Why it is not on `main` |
|---|---|---|
| `results/phase2/pilot_v1/` | first Claude Code pilot, 12 runs | docs leaked through `curl`; the harness was fixed (decision 30) |
| `results/phase2/pilot_discarded/` | Claude pilot runs broken by machine sleep | infrastructure, not the agent |
| `results/phase2/pilot/` | Claude Code pilot, 12/12 | the measured agent moved to Codex (decision 32) |
| `results/phase2/runs/` | 13 Claude Code sweep runs, all pass | sweep stopped to save the owner's Claude plan (decision 32) |
| `results/phase2/codex/pilot_gpt-6-astra/` | Codex pilot on `gpt-6-astra`, 11/12 | the model changed to `gpt-6.1-sol` (decision 33) |
| `results/phase2/codex/runs_set_aside_usage_limit/` | 26 runs that failed only because the Codex plan hit its usage limit | not agent outcomes; rerun (decision 34) |
| `results/phase3/judge_v1_t4_without_readme/` | first T4 judge passes, made without T4's README | superseded by the re-judge (decision 37) |

To get one back:

```bash
git fetch origin archive/all-runs-2026-11-16
```

```bash
git checkout origin/archive/all-runs-2026-11-16 -- results/phase2/pilot
```
