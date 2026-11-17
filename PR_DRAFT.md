# Proposed PR for `AssemblyAI/assemblyai-skill` (not opened yet)

Every number below comes from `results/phase3/headline.json` or `results/phase3/grades.csv` in this repo.

## Title

Add an execution-graded streaming eval and outcome-verified results

## Description

I used the skill while measuring how often coding agents ship AssemblyAI integrations that run cleanly but don't do what the user asked. The existing evals grade the generated code by reading it. This PR adds one eval that is graded by running the code and reading what the API actually applied, plus the results from 120 runs.

### What this adds

**One execution-graded eval: live clinic intake over streaming.** The prompt asks for real-time transcription of a phone call with medical vocabulary, speaker labels and PII masking, streamed from a file at real-time pace. The contract is `./run.sh <audio.wav> <out.json>`, and the app must also log every server message it receives, unmodified, to `<out.json>.frames.jsonl`.

The checker runs the app on a fixed 51-second fixture. It then reads the `Begin.configuration` the server echoed back, and passes only if all of these hold:

- the model is a current Pro streaming model (`universal-3-6-pro` or `universal-3-5-pro`)
- `domain` is `medical-v1`
- `speaker_labels` and `redact_pii` are true
- the final turns were logged
- the SSN digits from the fixture are absent from the saved output

Files to add:

- the prompt
- the fixture (`support_call.wav`, with its ground-truth manifest)
- the checker, which is a pure function over the run directory

All three are adapted from `runner/tasks/t1_stream_clinic/`, `fixtures/` and `twohundred/oracles.py:oracle_t1` in the [harness repo](https://github.com/aatif101/assemblyai-200-not-done).

Why this task: it was the only one of four where runs separated by condition. In 10 runs without the skill, 2 agents learned the streaming API from the installed SDK. They picked `universal-streaming-english`, saw it echoed in `Begin`, and shipped. The request was valid, so the API had nothing to warn about. With the skill, 20 of 20 runs used `universal-3-6-pro`.

A fully server-side alternative is the async redaction task. Its checker reads `GET /v2/transcript/{id}` instead of self-logged frames. It passed in all 30 runs, so it doesn't separate conditions here.

**A second results table, beside the existing one.** Method: Codex CLI with `gpt-6.1-sol`, headless in Docker, AssemblyAI docs hosts blocked, web search off. Four tasks (streaming, async, British spelling, legacy SDK upgrade), 10 runs per task per condition, 40 per condition. A run passes when a server-side check confirms the requested configuration and output, not when the code looks right.

| Condition | Runs that executed | Did what was asked (server-checked) | Ran but did not do it | Asked for a current model |
|---|---|---|---|---|
| Without skill | 40/40 | 38/40 | 2/40 | 12/40 |
| With skill | 40/40 | 40/40 | 0/40 | 40/40 |
| With skill + docs MCP | 40/40 | 40/40 | 0/40 | 40/40 |

At n = 40 the difference in the "ran but did not do it" column is not established (Wilson 95% intervals: 1.4–16.5% vs 0–8.8%). The current-model difference is (18–45% vs 91–100%), though it's a secondary measure, defined after the runs.

### Not included

Phase 1 of the harness found five places where the API's behavior differs from what the skill text says. They aren't in this PR because each needs a second check on another day first. I can follow up with them separately, each with the request and the response attached.
