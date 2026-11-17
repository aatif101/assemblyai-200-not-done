# Instructions for any agent working in this repo

You are picking up a three-phase measurement project part-way through. Everything you need is in `.planning/`. Read in this order before doing anything:

1. `.planning/STATE.md` — where the project is right now and the next action. Start here every session.
2. `.planning/PROJECT.md` — what is being built, for whom, and the rules that never bend.
3. `.planning/ROADMAP.md` — the three phases, their exit criteria, and what is checked off.
4. `.planning/DECISIONS.md` — decisions already made. Do not reopen them without the owner asking.
5. The plan or summary for the phase you are working on, under `.planning/phases/`.

If `.planning/OUTREACH.local.md` exists, read it too. It holds who the write-up is for and is deliberately kept out of git. If it is missing and you need that context (you only need it for the final message, not for the engineering), ask the owner.

## Rules that never bend

- **No invented numbers.** Every figure in a doc, chart or message comes from a file under `results/`. No placeholders, no "expected" values written as measured. If something has not been measured, say so.
- **Report what the data says**, including when it kills the headline. The alternatives are pre-registered in `.planning/phases/03-grading-writeup/PLAN.md`; pick whichever is true.
- **Documented behavior is not a finding.** AssemblyAI's skill already documents the silent-accept paths. The contribution here is measurement. Never present something their own skill states as a discovery.
- **No gotcha tone.** Neutral, specific, useful to the team that built the thing being measured.
- **Secrets.** Never print, log or commit an API key. Run `make secrets` before every commit (the pre-commit hook does it too). Agent transcripts must pass through the scrubber before they are written.
- **Nothing mocked.** Results come from real API calls and real agent runs. The loopback servers in `tests/` check plumbing only and never write to `results/`.
- **Do not open the pull request to AssemblyAI/assemblyai-skill.** Draft the title and description for the owner to review.
- **Flag ambiguity, then keep moving** on everything that does not depend on the answer.

## Working conventions

- Live API calls happen on the owner's machine (Windows), not in a cloud sandbox: sandboxes used so far could not reach `*.assemblyai.com`. Write live steps as one `make` target, give the exact command and expected output, and read results back from git.
- Classification and grading are separate from data collection and re-runnable offline from `results/`. Keep it that way so a classifier fix never needs new API calls.
- Anything that decides an outcome is written down before the data exists (`docs/PROBES.md` for Phase 1). If a rule is changed after seeing data, say so in the write-up.
- `make check` (tests + secret scan) must pass before a commit.
- At the end of a session, update `.planning/STATE.md`: what was done, what is next, anything newly open. Tick boxes in `.planning/ROADMAP.md`. Append to `.planning/DECISIONS.md` when the owner decides something.

## Commands

| Command | What it does | Needs key |
|---|---|---|
| `make setup` | virtualenv, dependencies, pre-commit secret hook | no |
| `make check` | tests and secret scan | no |
| `make plan` | print every probe request without sending | no |
| `make classify` | rebuild the Phase 1 matrix from `results/phase1/raw` | no |
| `make validate-fixtures` | check each audio clip carries its ground truth | yes |
| `make killtest`, `make phase1` | send the Phase 1 probes | yes |

The skill the probes quote is `AssemblyAI/assemblyai-skill` at commit `f584ec557e97ca9ce8151ec4d3b6e2ec8e3ade75`. Clone it next to this repo (or set `SKILL_REPO`) to run the verbatim-quote test.
