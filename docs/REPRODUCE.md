# Reproduce and read the details

The [README](../README.md) has the result. This page has how to re-run it and what each part measures.

## What this repo contains

AssemblyAI's skill already documents that some wrong requests are accepted without an error (unknown streaming query params are ignored, `u3-rt-pro` is redirected, Medical Mode is skipped for unsupported languages). Those behaviors are theirs and documented. This repo measures what follows from them: how often real agents land on those paths, whether the skill and docs MCP prevent it, and which server responses would close the gap.

Project state and every decision: [`.planning/STATE.md`](../.planning/STATE.md), [`.planning/DECISIONS.md`](../.planning/DECISIONS.md).

| Phase | What | Results |
|---|---|---|
| 1 | Probe matrix: 51 known-wrong requests across async, streaming, sync, dictation and TTS, each classified from the server's response | [`results/phase1/matrix.md`](../results/phase1/matrix.md) |
| 2 | Agent runs: 4 tasks × 3 conditions × n = 10, headless Codex CLI in Docker, docs hosts blocked | [`results/phase2/codex/runs/`](../results/phase2/codex/runs/) (every transcript, scrubbed); superseded runs: [`results/ARCHIVE.md`](../results/ARCHIVE.md) |
| 3 | Three verdicts per run, failure list, charts, write-up, PR draft | [`results/phase3/headline.md`](../results/phase3/headline.md), [`grades.csv`](../results/phase3/grades.csv), [`failures.csv`](../results/phase3/failures.csv) |

## Reproduce everything

```bash
make all
```

That runs, in order: setup and tests, Phase 1 probes, the Phase 2 pre-checks and reference check, the 120-run sweep, the judge, account-id redaction and grading. It needs `ASSEMBLYAI_API_KEY` in `.env`, Docker running, and a signed-in Codex CLI (`codex login`, ChatGPT plan). Measured cost of the expensive steps on the owner's run: the sweep's agents worked 5.4 hours in total (3 lanes in parallel, streaming runs one at a time) and used 55.5 million tokens; the judge made 312 calls, 4.7 million tokens in total (median 13.7k per call). AssemblyAI usage was not metered per run; every test clip is under a minute long, and each run sends it at least once (the harness's own run) plus however often the agent tested.

To rebuild every Phase 3 number and both charts from the committed results, offline and without any key:

```bash
make grade
```

## Run Phase 1

Requirements: Python 3.10+, `make`, an AssemblyAI API key. No Docker, no ffmpeg (the audio is committed).

```bash
make setup
```

```bash
cp .env.example .env   # then put your key in .env; it is git-ignored
```

```bash
make check
```

Expected: `46 passed, 1 skipped`, then `secret check passed: … files scanned`. The skipped test checks that every sentence attributed to the skill is a verbatim quote; it runs (`47 passed`) when a checkout of `AssemblyAI/assemblyai-skill` at `f584ec5` is next to this repo or at `$SKILL_REPO`.

```bash
make validate-fixtures
```

Expected: nine `PASS`/`FAIL`/`note` lines and `results/phase1/fixture_validation.json`. Eight transcriptions, about three minutes of audio in total. A `FAIL` on a required check means that clip cannot serve as ground truth; rebuild it with natural voices (`make fixtures BACKEND=aai-tts`) and validate again.

```bash
make killtest
```

Expected: about 40 lines like `[7/40] stream.param.model_name mistake#0: Begin received`, then a ten-row table and one line: `KILL TEST: N/10 rejected loudly -> PROCEED …` or `PIVOT …`. It should take a few minutes; most of that is waiting for async transcripts. If the line says `INCOMPLETE`, some control request failed; the table shows which.

```bash
make phase1
```

Expected: about 200 requests, then the full table. Not timed yet; budget 20 minutes. Already-recorded requests are skipped, so this resumes after an interruption. Outputs:

| File | Content |
|---|---|
| `results/phase1/matrix.md` | the probe matrix, one row per probe |
| `results/phase1/matrix.csv`, `matrix.json` | the same, machine-readable, with a summary by class and surface |
| `results/phase1/killtest.md` | the ten kill-test rows and verdict |
| `results/phase1/raw/*.json` | every request and response, scrubbed |

`make classify` rebuilds the matrix from `raw/` without calling the API.

Environment variables: `ASSEMBLYAI_API_KEY` (required for the live targets). `SKILL_REPO` (optional, path to the skill checkout for the quote test). `REPEATS` (optional, default 2 sends per request).

## What Phase 1 measures

Each probe is a mistake an agent plausibly makes, paired with a control: the same request with the mistake corrected. 42 probes come from the skill's own text, quoted verbatim and pinned to commit `f584ec5`; 9 generic ones are labelled `author`. The list, the tokens that count as "the error names the fix", and the kill-test threshold were written before any request was sent: [docs/PROBES.md](PROBES.md).

A response is classified by rule, from server-side evidence only: the config echoed by `GET /v2/transcript/{id}`, `speech_model_used`, `metadata.warnings`, the streaming `Begin.configuration`, response headers, and the bytes returned.

| Class | Meaning |
|---|---|
| instructive error | rejected, and the message names the fix |
| opaque error | rejected, and it does not |
| silent accept | accepted, intended effect not applied, no warning anywhere in the response |
| accepted with warning | accepted, with a warning the corrected request does not carry |
| accepted and applied | the input still works |
| accepted, effect unverifiable | nothing in the response can confirm or refute it |

The matrix also has an `agrees` column: whether the API did what the skill says it does for that input.

## Secrets

The key is read from the environment or `.env` and held in memory. The `Authorization` header is never recorded, only the scheme used. Everything written to `results/` passes through a scrubber that also catches URL-encoded and base64 forms, and the writer refuses to write if a secret survives.

`make secrets` scans every file git would commit, plus all of `results/`, for the exact value of every secret-looking environment variable and for key-shaped strings. It exits 1 on a hit and never prints the match. It runs in CI and as a pre-commit hook (`make setup` installs it).

## Limits of Phase 1

- The audio is synthetic (espeak-ng voices). `make validate-fixtures` checks each clip against the live API before it is used; it does not make the clips natural speech.
- "Applied" means the server echoed or reported the setting. Whether a key term actually changed the transcript is not measured here.
- Where a response carries no echo (Sync, streaming key terms), the row says "unverifiable" instead of guessing.
- "Names the fix" is a substring match against tokens fixed in advance. `matrix.csv` keeps the raw error excerpt so any row can be re-judged.
- The tests under `tests/` use a loopback server and hand-written records to check plumbing and the decision table. They are not API responses and produce no results.
