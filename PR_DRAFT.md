# PR to `AssemblyAI/assemblyai-skill`

Opened by the owner on 2026-11-17: [AssemblyAI/assemblyai-skill#37](https://github.com/AssemblyAI/assemblyai-skill/pull/37). The text below is what was submitted.

Branch: `execution-eval-streaming`, one commit on top of `f584ec5`. Every number below comes from `results/phase3/headline.json`, `results/phase3/grades.csv` or `results/phase3/t1_models.csv` in this repo.

## Title

Add an execution-graded streaming eval and outcome-verified results

## Description

The skill's public evals grade the code an agent writes by reading it. I wanted to see what happens if you run that code and check what the API actually applied, so I built a harness for it and ran it 120 times. You may already test this way internally; if so, I'd be glad to hear how it compares.

This PR adds the one task from that work that separated with-skill and without-skill runs, as an eval you can run, plus the results.

### What's in it

- **`evals/execution/streaming-clinic-intake/`**: a prompt (live clinic intake over streaming: medical vocabulary, speaker labels, PII masking), a 51-second fixture with its ground truth, and `check.py`. The checker runs the agent's `./run.sh` on the fixture, then passes only if the server's `Begin.configuration` shows a current Pro model, `medical-v1`, speaker labels and PII redaction, and the fixture's SSN and phone digits are absent from the output. Standard library only. `--score` re-grades a saved run offline.
- **`tests/test_execution_eval_check.py`**: offline tests for the checker. No network.
- **README**: a second results table next to the existing one.

### Results

Codex CLI with `gpt-6.1-sol`, AssemblyAI docs hosts blocked, four tasks, 10 runs per task per condition:

| Condition | Did what was asked | Ran but did not do it | Asked for a current model |
|---|---|---|---|
| Without skill | 38/40 | 2/40 | 12/40 |
| With skill | 40/40 | 0/40 | 40/40 |
| With skill + docs MCP | 40/40 | 0/40 | 40/40 |

On the streaming task, both failures without the skill picked `universal-streaming-english` from the Python SDK's `SpeechModel` enum; the request was valid, so nothing warned the agent. Six more asked for the removed `u3-rt-pro`, and the server ran `universal-3-6-pro`. With the skill, all 20 used `universal-3-6-pro`.

At n = 40 the "ran but did not do it" difference is not statistically established (Wilson 95%: 1.4–16.5% vs 0–8.8%). The current-model difference is clear (18–45% vs 91–100%)), though it's a secondary measure, defined after the runs.

### Checked

- `check.py` re-graded the 30 saved streaming runs and agreed with the harness on all 30.
- `pytest tests/test_execution_eval_check.py`: 5 passed.
- Live mode, run on 2026-11-17 against the API on two saved apps: the with-skill app passed every check, and the without-skill app that picked `universal-streaming-english` failed only `model_current_pro`, the same results as in the original runs.

Every run, the harness and the write-up: https://github.com/aatif101/assemblyai-200-not-done
