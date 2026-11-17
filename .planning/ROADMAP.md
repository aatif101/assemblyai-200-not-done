# Roadmap

Three phases. Each has a gate the owner controls. Update the checkboxes as work lands; `STATE.md` says which box is next.

| Phase | Name | State | Plan / summary |
|---|---|---|---|
| 1 | Probe matrix | **Run. Follow-ups open.** | `phases/01-probe-matrix/SUMMARY.md` |
| 2 | Agent runs | **Done.** Codex sweep 120/120: 118 outcome, 2 silent failures. Claude data partial (decision 32). | `phases/02-agent-runs/PLAN.md` |
| 3 | Grading, write-up, PR draft | **Drafted.** Waiting on the owner's review and the PR. | `phases/03-grading-writeup/PLAN.md` |

## Phase 1: probe matrix

Question: when a request is wrong in a way an agent plausibly gets wrong, does the API say so?

- [x] Probe list written and frozen before any request (`docs/PROBES.md`, 51 probes, 42 quoted from the skill).
- [x] Runner, classifier, report, secret scrubber and scan, tests.
- [x] Audio fixtures with known ground truth, built and committed.
- [x] Fixture validation run against the live API (7 of 9 checks pass; `verbatim_memo` fails a required check).
- [x] Kill test run: 4 of 10 loud, threshold 8, **proceed**.
- [x] Full matrix run: 51 probes decided.
- [x] F1 classifier fix (`async.word_boost` should be "accepted with warning").
- [x] F2 effect checks (one row now decided; two cannot be decided from a transcript, reason recorded).
- [ ] F5 re-check the five skill disagreements on another day.
- [x] F6 remove account identifiers from saved records (`scripts/redact_account_ids.py`; still in git history).

Gate: the owner ran Phase 1, pushed the results, and gave the streaming decision Phase 2 was waiting on (decision 16). The pre-registered kill rule says proceed.

## Phase 2: agent runs

Question: how often do real agent runs end in code that runs and did not do what was asked, per condition?

- [x] Owner answers Q1 to Q6 in the Phase 2 plan (took all recommendations, 2026-11-14).
- [x] Tasks frozen: prompts, schemas, rubrics, T4 seed repo (`9679196`).
- [x] Live pre-checks for T3 and T4 feasibility (`ffb29c0`: British spelling gradable; summarization works).
- [x] Oracles written, tested, committed before any sweep run; reference check 8 of 8 live (`9679196`).
- [x] Docker runner, `make pilot`, `make sweep` (`aee7de0`; not yet run).
- [x] Pilot: 12 runs, every transcript read (three passes; final 12/12, decisions 30 and 31).
- [ ] **Gate: owner sees the pilot and says go.**
- [x] Codex pilot: 12 runs, every run checked (decision 32).
- [x] Sweep: 120 runs (Codex): 120 runs, 118 outcome, 2 silent failures. Claude sweep paused at 13.
- [x] Secret scan over all transcripts, clean.

## Phase 3: grading, write-up, PR draft

Question: what does the data say, and what would be useful to the team that owns the skill?

- [x] Three verdicts per run (`results/phase3/grades.csv`).
- [x] Headline with Wilson intervals; reading: silent failures are rare everywhere.
- [x] Failure taxonomy linked back to Phase 1 probes (`failures.csv`, `t1_models.csv`).
- [x] Continuity re-grade of the three committed eval outputs.
- [x] Two charts, generated from `results/` (`make grade`).
- [x] `WRITEUP.md`, 600 words or fewer (595, tables included).
- [ ] `PR_DRAFT.md` for the owner; **the owner opens the PR.**
- [x] README first three lines with real values; `make all`; PR link added (#37).
- [ ] Pre-publication checks.
- [x] One-line summary with real numbers (`STATE.md`, D4).

## Deliverable map

| Deliverable | Produced in |
|---|---|
| D1 repo, one command, scrubbed transcripts | all phases; finalized in 3 |
| D2 write-up | 3 |
| D3 PR draft | 3 |
| D4 one-line summary | 3 |
