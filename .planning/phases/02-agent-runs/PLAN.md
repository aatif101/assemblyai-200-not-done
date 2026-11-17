# Phase 2 plan: agent runs

**Status: not started. Nothing below has been built.** All design questions are settled: on 2026-11-14 the owner took every recommendation in Q1 to Q6. Two of those (Q3, Q4) depend on a live check whose result picks the branch; one number (the per-run spend cap) is still the owner's to give.

## Goal

120 real runs of headless Claude Code building AssemblyAI integrations: 4 tasks × 3 conditions × 10 runs. Every run leaves a scrubbed transcript, the code it produced, and the output of that code executed by the harness, so Phase 3 can grade each run three ways without calling an agent again.

## Decided

- **Agent:** Claude Code, headless (`claude -p`), inside Docker. One agent only.
- **Conditions:**
  1. `bare` — no skill, no MCP server.
  2. `skill` — `skills/assemblyai/` from `AssemblyAI/assemblyai-skill` at `f584ec5`, installed as a project skill.
  3. `skill+mcp` — the skill plus AssemblyAI's docs MCP server (`https://www.assemblyai.com/docs/mcp`, no auth, per the skill).
- **Sample:** n = 10 per cell. Pooled by condition (n = 40) is the headline.
- **Runner:** plain Python in this repo. Not inspect_ai.
- **Fixed CLI contract** per task, so the harness can execute any run's code the same way.
- **Per-run caps** on turns, wall-clock time and spend. A run that hits a cap is a failed run, never dropped.
- **Gate:** pilot one run per cell (12 runs), show the owner, then launch the 120.
- **Streaming task (owner, 2026-11-14):** the task is shaped around settings that the streaming `Begin.configuration` message echoes back, so the outcome is verified from the server. The two streaming mistakes that leave no echo (`word_boost`, and v2-era silence parameters such as `end_utterance_silence_threshold`) are checked separately by reading the agent's code, reported as their own count, and **kept out of the headline number**.

`Begin.configuration` echoes exactly: `model`, `mode`, `api_version`, `speaker_labels`, `redact_pii`, `filter_profanity`, `domain`, `voice_focus` (confirmed in Phase 1 records).

## The four tasks

Each task needs: a prompt written the way a user would write it (no parameter names, no hints), a fixture, a CLI contract, an outcome oracle that reads server-side evidence, and a "looks right" rubric in the style of AssemblyAI's `evals.json` assertions. Prompts below are intent, not final wording; finalize them in step 2 of the build list and freeze them before the pilot.

### T1. Live call transcription (streaming)

- **User intent:** transcribe live clinic intake calls. Medical vocabulary matters. Label who is speaking. Mask personal details as they are spoken. Callers read numbers slowly with pauses, so favor accuracy over speed.
- **Fixture:** `support_call.wav`, streamed at real-time pace by the app. Its content is an insurance call, not a medical one; the oracle reads configuration, not content, so this does not affect grading. A clinic-flavored clip can be added with `fixtures/scripts/` if the mismatch distracts the agent in the pilot.
- **Outcome oracle (all from `Begin.configuration`):** `domain == "medical-v1"`, `speaker_labels == true`, `redact_pii == true`, `mode == "max_accuracy"`, `model` is a current Pro streaming model. Plus: SSN digits absent from the saved final turns.
- **Secondary, not in the headline:** static scan of the run's source for parameter names that are not part of the v3 API (`word_boost`, `end_utterance_silence_threshold`, and camelCase forms sent as query parameters). Report the count separately.
- **Phase 1 facts that matter:** unknown parameter names are ignored; bad values of real parameters are rejected with the allowed values listed; streaming accepted `domain=medical` and applied `medical-v1`.

### T2. Call redaction and speakers (async)

- **User intent:** transcribe recorded support calls, tell the two speakers apart, and make sure social security numbers, phone numbers and names never reach the stored transcript.
- **Fixture:** `support_call.wav` (validated: SSN audible unredacted, gone when redacted, two speakers found).
- **Outcome oracle:** from `GET /v2/transcript/{id}`: `redact_pii == true`, `redact_pii_policies` includes `us_social_security_number`, `speaker_labels == true`. From the saved output: SSN digits `987654321` absent (use `fixtures.contains_digits`), at least two speakers.
- **Phase 1 facts:** a wrong policy name (`ssn`) is rejected with the full list of valid names.

### T3. British spelling, or key terms (decided by a live check, see Q3)

Originally "transcribe verbatim, British spelling". The verbatim half cannot be graded: the default transcript already keeps fillers for the clip (5 with `disfluencies: true`, 5 without). The British-spelling half may be gradable: the default transcript used American spellings, and the skill says `language_detection_options.localization: "en_uk"` applies regional spelling and surfaces `en_uk` in `language_code`.

### T4. Upgrade a legacy integration (async)

- **User intent:** "this script stopped working / gives worse results; get it working on the current API." The seeded repo uses patterns from the pre-1.0 Python SDK and retired parameters.
- **Seed repo (to write):** a small script on `assemblyai` 0.x that uses singular `speech_model`, `word_boost` for product names, and speaker labels, run against `product_memo.wav`. Whether it also uses LeMUR for a summary is part of Q4.
- **Outcome oracle:** runs on a current SDK; from the transcript object, the product names reach the server as `keyterms_prompt`; in the saved output, all three invented product names are spelled exactly (validated: 0 of 3 with no options, 3 of 3 with `keyterms_prompt`).
- **Phase 1 facts:** singular `speech_model` is rejected with the fix in the message. `word_boost` returns 200 and is skipped, with a notice only in `metadata.warnings`. So an agent that fixes the loud error and stops has code that runs and silently loses the boost. This is the cleanest silent-failure oracle available.

## CLI contract

Every task's prompt ends with the same kind of requirement, identical across conditions, for example for T2:

> Put the entry point at `./run.sh`. It is called as `./run.sh <audio.wav> <out.json>` and must write `<out.json>`.

The output schema is given per task (for example `{"transcript_id": "...", "text": "...", "utterances": [{"speaker": "...", "text": "..."}]}`). For T1 the app must also write every raw server message it receives, in order, to `<out>.frames.jsonl`; the oracle reads `Begin` from there (see Q2).

After the agent stops, the harness, not the agent, runs `./run.sh` in a fresh process inside the same container against the fixture, with a timeout, and saves exit code, stdout, stderr and the output files.

## Runner design

```
runner/
  Dockerfile            python 3.12, node 22, Claude Code CLI, nothing task-specific
  run_one.py            one (task, condition, index): build workspace, start container, run agent, run contract, collect
  run_all.py            the 12-run pilot or the 120-run sweep; resumable; fixed order with a recorded seed
  tasks/<task>/         prompt.md, schema.json, rubric.json, seed/ (T4 only)
  conditions/           how each condition changes the workspace and the claude flags
results/phase2/runs/<task>/<condition>/<nn>/
  meta.json             task, condition, index, model id, claude --version, image digest, skill commit, caps, start/end, cap hit?
  agent.jsonl           stream-json from claude, each line stamped with receive time, scrubbed
  workspace/            the files the agent left (minus .venv, node_modules, audio)
  contract.json         exit code, duration, stdout/stderr (scrubbed), which output files exist
  out.json, out.frames.jsonl
  server/               transcript objects fetched by the harness with GET for every id the run reports
```

Requirements:

- Keys enter the container as runtime environment variables only. Never in the image, never in a file under `results/`.
- Every file written under `results/phase2/` goes through `twohundred.secrets.Scrubber`, and `make secrets` runs at the end of the sweep. Agents echo environment variables and curl commands into transcripts; assume the key will appear and prove it does not survive.
- The same prompt text, fixture and caps for all three conditions of a task. Only the skill directory and the MCP config differ.
- Pin and record: the model id, `claude --version`, the image digest, the skill commit.
- Resumable: a run directory with a complete `meta.json` is skipped.
- Pilot runs are excluded from the 120 and kept under `results/phase2/pilot/`.
- The container needs outbound access to the AssemblyAI API hosts, the Anthropic API, PyPI and npm, and for `skill+mcp` the docs host.

## Settled questions (owner took all recommendations, 2026-11-14)

**Q1. Web access for the agent.** With WebSearch/WebFetch on, the bare condition can simply read AssemblyAI's docs and "bare" stops meaning anything. **Decided:** web tools are disabled in all three conditions, so the documentation sources are exactly: training data, the skill, the docs MCP. State this in limitations.

**Q2. Where the streaming `Begin` comes from.** There is no server endpoint that returns a past session's configuration. **Decided: option A.** The contract requires the app to log raw frames; the oracle reads `Begin` from the log. It is self-reported by the code under test, so check the 12 pilot runs by hand and say so in limitations. Option B (a TLS-intercepting proxy in the container, reading frames off the wire) is not being built: websocket clients differ in whether they honor proxy settings, and it is real plumbing.

**Q3. What T3 is.** **Decided:** first spend one live check on whether British spelling is gradable: transcribe `verbatim_memo.wav` with the localization setting and confirm British spellings in the text and `en_uk` in `language_code`. Language detection wants at least 15 seconds of speech and the clip is 12, so it may need a longer clip. If the check passes, T3 is "British spelling for a UK customer". If it fails, T3 is a greenfield key-terms task on `product_memo.wav`, which already has a validated oracle. The check has not been run.

**Q4. LeMUR in the T4 seed repo.** LeMUR removal is the most realistic upgrade, but its replacement needs either the LLM Gateway (not on free accounts, per the skill) or Speech Understanding summarization (open beta). **Decided:** check once that `speech_understanding.request.summarization` works on the owner's account. If yes, the seed includes a LeMUR summary and either replacement is accepted. If no, LeMUR is left out. The check has not been run.

**Q5. Time to first working call.** Needs a rule that can be applied mechanically. **Decided:** from `agent.jsonl`, the first tool result in which the agent's own execution of its code exits 0 after a request to an AssemblyAI host; record seconds and tokens up to that event, and `null` if it never happens. Validate the rule by hand on the pilot runs before the sweep. Running lanes in parallel is fine, since the rule does not depend on server-side listing.

**Q6. Caps.** **Decided:** 40 turns and 20 minutes wall clock per run. Revisit after the pilot. **Still needed from the owner:** a per-run spend cap. Until one is given, the turn and time caps are the only limits; do not invent a dollar figure.

## Build list

1. [x] Owner answers Q1 to Q6 (2026-11-14: all recommendations taken; spend cap number outstanding).
2. [ ] Write the four `tasks/<task>/` directories: prompt, output schema, rubric, and the T4 seed repo. Freeze and commit them before any run.
3. [ ] Live pre-checks on the owner's machine for Q3 and Q4, as a `make` target; commit the results.
4. [ ] Write the oracles as pure functions over a run directory, with tests on hand-written inputs (same pattern as `classify.py`). Oracles are part of the pre-registration: commit them before the sweep.
5. [ ] `Dockerfile`, `run_one.py`, `run_all.py`, `make pilot`, `make sweep`.
6. [ ] `make pilot` on the owner's machine: 12 runs. Review every transcript by hand. Fix the harness, not the tasks.
7. [ ] Show the owner the pilot. On a yes, `make sweep`.
8. [ ] `make secrets` over `results/phase2/`. Commit.

## Exit criteria

- 120 run directories, each with a complete `meta.json`, plus 12 pilot runs.
- Zero secret hits.
- Prompts, schemas, rubrics and oracles were committed before the first sweep run (check the git log).
- Any cap hits, harness failures or container errors are counted and listed, not hidden.
