# State

**Last updated:** 2026-11-16, by the agent working on the owner's Windows machine (Claude Code, local).
**Update this file at the end of every working session.**

## Where we are

**All three phases are run, graded and published.** The public copy is [`aatif101/assemblyai-200-not-done`](https://github.com/aatif101/assemblyai-200-not-done), a single clean commit of this repo's `main` (decision 39). The owner reviews `WRITEUP.md` and `PR_DRAFT.md` and opens the PR.

- Lead (decision 38): asked for a current Pro model 12/40 bare, 40/40 skill, 40/40 skill + MCP. Pre-registered headline (reading: "silent failures are rare everywhere"): 2 of 120 runs ran clean without doing what was asked, 2/40 bare (5%, 95% CI 1.4-16.5%), 0/40 skill, 0/40 skill + docs MCP (0-8.8%). Difference not established. Every figure: `results/phase3/headline.md` / `headline.json`, rebuilt offline by `make grade`.
- Failures (`results/phase3/failures.csv`): both are T1 bare, `universal-streaming-english` picked from the SDK's `SpeechModel` enum. Related, not failures: 6 T1 bare runs asked for removed `u3-rt-pro` and the server ran `universal-3-6-pro` (`t1_models.csv`); bare async runs used `universal-2` (14) or superseded `universal-3-pro` (6) (`headline.json` `async_models`).
- `looks_right` (judge, decisions 35 and 37): 32/40 bare, 40/40, 40/40; two passes agree 120/120; hand check 0 wrong of 114 assertions (`judge_handcheck.csv`). It caught both silent failures and passed nothing the server check failed.
- First working call (decision 36): median 55 s bare, 81 s skill, 85 s skill + MCP; median tokens 314k / 365k / 469k.
- Continuity: the skill repo's committed with-skill outputs pass 14 of 18 of today's assertions (17 of 17 under their own).
- Superseded runs are on the branch `archive/all-runs-2026-11-16` (`results/ARCHIVE.md`).

## Next actions, in order

1. **Owner:** read `WRITEUP.md` and `PR_DRAFT.md`; choose the eval for the PR (streaming proposed, async redaction is the fully server-side alternative); open the PR yourself; then put its link in the README's "Three small fixes" line and run `scripts/publish_public.sh` to update the public copy.
2. **Owner, live, any later day:** F5 (re-check the five skill disagreements) and the dictation `language_codes: "it"` probe (decision 25). PR_DRAFT leaves the disagreements out until F5 is done.

## Open items

- **Five skill disagreements** are one account on one day (F5).
- **`verbatim_memo.wav` still fails the verbatim check** (fillers kept by default). It is used only for spelling, which the pre-check showed works. `fixture_validation.json` still says `passed: false`.
- **AssemblyAI usage** was not metered by the harness. Check the account dashboard before quoting a cost.

## Environment notes

- Windows, Git Bash. `make` works. The Makefile picks `.venv/Scripts/python.exe` when it exists.
- A Claude Code permission rule stops agents reading `.env`; the owner edits it.
- Docker runs the agents (`twohundred-runner` image). Streaming sessions back to back can hit "Too many concurrent sessions"; the runner defaults to one lane (`PARALLEL=1`).
- The skill is cloned at `../assemblyai-skill` (pinned `f584ec5`); the runner checks the commit before every skill run.
- GitHub: `aatif101/200-not-done` is the private working repo (full history, default branch `main`). `aatif101/assemblyai-200-not-done` is the public copy, written only by `scripts/publish_public.sh`.

## Session log

| Date | Who | What |
|---|---|---|
| 2026-11-14 | agent | Researched AssemblyAI's agent tooling, proposed the project, built the Phase 1 harness, fixtures and tests. Could not reach the API; nothing run. |
| 2026-11-14 | owner | Created the repo, ran fixture validation, kill test and full matrix, pushed results, fixed two Windows issues. Decided the streaming check (decision 16). |
| 2026-11-14 | agent | Read the results, wrote `.planning/` and `AGENTS.md`. Found F1 and F6 while reading. No code changed. |
| 2026-11-14 | owner | Took all six Phase 2 recommendations (decisions 18 to 23). Work moves to the owner's local machine from here. |
| 2026-11-15 | agent (local) | F1, F2. Pre-checks written, committed, run. Tasks, oracles, reference check (8/8), runner. Owner gave the $5 budget (decision 24). Decisions 25 to 28. |
| 2026-11-15 | owner + agent (local) | Owner chose the Claude subscription (decision 29). Pilot run three times; harness fixes (decisions 30, 31). Final pilot 12/12. |
| 2026-11-15 | owner + agent (local) | Claude sweep paused at 13/120 (all pass) to save the Claude plan. Owner switched the measured agent to Codex (decision 32). Codex runner built; Codex pilot 12 runs, 11 outcome, 1 silent failure. |
| 2026-11-15/16 | owner + agent (local) | Model set to gpt-6.1-sol (decision 33); its pilot 11/12. Sweep stopped at the Codex usage limit with 44 clean runs; limit-hit runs set aside, runner guard added (decision 34). |
| 2026-11-16 | owner + agent (local) | Sweep finished 120/120. Phase 3 run: judge (decisions 35, 37), first-call rule (36), F6 redaction, grades, charts, write-up, PR draft, README. |
| 2026-11-16 | owner + agent (local) | Current-model measure (decision 38). Superseded runs moved to the archive branch. Public copy published from a clean history (decision 39). |
