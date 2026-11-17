# Phase 1 summary: probe matrix

**Status: run and classified on 2026-11-14; F1 and F2 applied offline on 2026-11-15 (classifier changed after the data was seen, see below). F3 to F6 still open.**

## What was done

51 known-wrong requests were sent to five API surfaces, each paired with a control (the same request, corrected), two repeats each: 204 saved records. Every row was decided by rule from the server's response. The probe list, the tokens that count as "the error names the fix", and the kill threshold were fixed in `docs/PROBES.md` before any request was sent.

- Code: `twohundred/probes.py` (the list), `twohundred/transport.py` (send and record), `twohundred/classify.py` (decision table), `twohundred/cli.py`.
- Data: `results/phase1/raw/*.json` (scrubbed), `results/phase1/matrix.{md,csv,json}`, `results/phase1/killtest.md`, `results/phase1/fixture_validation.json`.
- Rebuild the matrix offline: `make classify`.

## Kill test

4 of 10 kill probes were rejected loudly. The pivot threshold was 8. **Verdict: proceed.** The "API rejects everything loudly" alternative is ruled out as the overall story.

## The matrix as classified (51 probes, all decided, no repeats disagreed)

| Class | Count |
|---|---|
| instructive error (rejected, message names the fix) | 17 |
| opaque error (rejected, message does not) | 10 |
| silent accept (accepted, effect not applied, no warning) | 6 |
| accepted with warning | 3 |
| accepted and applied | 10 |
| accepted, effect unverifiable | 5 |

By surface:

| Surface | Instructive | Opaque | Silent | Warned | Applied | Unverifiable |
|---|---|---|---|---|---|---|
| async (17) | 5 | 5 | 1 | 1 | 4 | 1 |
| streaming (13) | 3 | 2 | 5 | 0 | 3 | 0 |
| sync (6) | 1 | 1 | 0 | 0 | 1 | 3 |
| dictation (7) | 4 | 1 | 0 | 0 | 1 | 1 |
| tts (8) | 4 | 1 | 0 | 2 | 1 | 0 |

These are the counts as first classified on 2026-11-14. **After F1 and F2 (2026-11-15, offline, no new API calls) `results/phase1/matrix.*` reads: 17 instructive, 10 opaque, 6 silent, 4 warned, 10 applied, 4 unverifiable.** Two rows moved: `async.word_boost` applied → warned (F1) and `sync.audio.mp3` unverifiable → applied (F2). By surface after the fix: async 5/5/1/2/3/1, sync 1/1/0/0/2/2; the other surfaces are unchanged.

### The six silent accepts

| Probe | What happened |
|---|---|
| `stream.param.model_name` | `model=universal-streaming-english` ignored; `Begin` reports `universal-3-6-pro` |
| `stream.model.u3_rt_pro` | `speech_model=u3-rt-pro` accepted; `Begin` reports `universal-3-6-pro` |
| `stream.param.camel_case` | `speakerLabels=true` ignored; `Begin` reports `speaker_labels: false` |
| `stream.param.word_boost` | not a v3 parameter; session opened with no error |
| `stream.param.v2_silence` | `end_utterance_silence_threshold` is not a v3 parameter; session opened with no error |
| `async.speech_models.universal` | `["universal"]` accepted; `speech_model_used` is `universal-2` |

Five of the six are on streaming, and all five are the unknown-parameter behavior the skill already documents. They confirm the documented behavior; they are not new.

### How each surface treats a field it does not know (measured)

| Surface | Response |
|---|---|
| async | HTTP 400 `Invalid endpoint schema, please refer to documentation for examples.` The field is not named. |
| streaming | Unknown parameter *names* are ignored. Bad *values* of real parameters are rejected with the allowed values listed (close 3006). |
| sync | HTTP 400 naming the field (`speaker_labels: Extra inputs are not permitted`). |
| dictation | HTTP 400 naming the field. |
| tts | HTTP 200, field listed in the `x-tts-unknown-params` header. |

### `Authorization: Bearer KEY`

Accepted on async, sync, dictation and TTS. Rejected on streaming (error frame, close 1008, `Invalid API key`). The skill calls Bearer on async the number-one mistake without saying how the API responds, so this is recorded as an observation, not a disagreement.

### Useful for Phase 2

- **Key terms have a behavioral oracle that works.** In fixture validation, `product_memo.wav` came back with 0 of 3 invented product names spelled right with no options and 3 of 3 with `keyterms_prompt`.
- **`word_boost` on async is accepted and skipped.** HTTP 200, `speech_model_used: universal-3-5-pro`, and `metadata.warnings` says `Skipped word_boost because it is not a supported feature on Universal-3 Pro.` An agent that does not read `metadata` ships code that runs and loses the boost.
- **Singular `speech_model` is rejected with the fix in the message.** A run-and-fix agent should recover from this one unaided.
- **Translation under `speech_understanding.request` works on the owner's account** (the control for that probe succeeded), so Speech Understanding is available for task design.
- `GET /v2/transcript/{id}` echoes `redact_pii`, `redact_pii_policies`, `speaker_labels`, `speech_models`, `speech_model_used`, `keyterms_prompt`, `word_boost`, `language_code`, `disfluencies`, `prompt`, `summarization`, `speech_understanding`.

## Where the API did something other than what the skill says

Each of these is one account on one day. **Re-check before stating any of them publicly** (follow-up F5).

| Probe | Skill at `f584ec5` says | Observed |
|---|---|---|
| `async.sentiment.non_english` | completes with `sentiment_analysis_results: null`, no error | HTTP 400, `The following models are not available in this language: sentiment_analysis` |
| `async.speech_models.u3_pro` | `["universal-3-pro"]` returns an error | accepted, `speech_model_used: universal-3-pro` |
| `sync.audio.mp3` | 415 unsupported media type | HTTP 200 with a correct transcript |
| `stream.url.v2` | v2 still works but is legacy | websocket handshake HTTP 404 |
| `stream.pacing.fast` | close 3007 when audio is sent faster than real time | 12.1 s of audio sent unpaced, session ended normally |

Smaller drift, same caveat:

- `u3-rt-pro` was redirected to `universal-3-6-pro`; the skill says `universal-3-5-pro`.
- `["universal"]` ran `universal-2`; the skill says it routes to `universal-3-5-pro`.
- `speech_model=universal-3-6` was accepted and echoed as `universal-3-6`. The server's own error for an invalid model lists it among allowed values, along with `u3-rt-pro` and several values the skill does not mention.
- Streaming accepted `domain=medical` and reported `medical-v1`. Async rejected the same value and listed the valid ones.
- Top-level `summarization` still returned a summary, with a warning that such requests would return an error starting 2026-09-15.
- Dictation accepted `language_codes: "en"` as a string; the skill says it is always a list.

These are candidates for the documentation part of the PR (Phase 3), once re-checked.

## Fixture validation

Backend: espeak-ng. 7 of 9 checks passed; the file reports `passed: false` because one required check failed.

| Clip | Check | Result |
|---|---|---|
| `support_call` | SSN audible with no redaction | pass |
| `support_call` | two speakers separated | pass (`A`, `B`) |
| `support_call` | SSN absent with redaction on | pass |
| `support_call` | phone number absent with redaction on | pass |
| `product_memo` | product names exact with `keyterms_prompt` | pass, 3/3 |
| `product_memo` | product names exact with no options | 0/3 (informational) |
| `verbatim_memo` | fillers kept only with `disfluencies: true` | **fail**: 5 fillers with the option, 5 without |
| `short_it`, `short_en` | content transcribed | pass |

The `verbatim_memo` failure means the default transcript already keeps "um" and "uh" for this clip, so "did the agent get fillers kept" cannot separate a right integration from a wrong one. The same clip did come back with American spellings by default (`organize`, `color`, `center`, `cataloged`), so a British-spelling check may still work. This drives open question Q3 in the Phase 2 plan.

## Follow-ups

- [x] **F1. Classifier: a warning that names the input overrides an echo.** `async.word_boost` is classed "accepted and applied" because the transcript echoes `word_boost`, but `metadata.warnings` says it was skipped. Rule to add in `classify_one`: if a warning not present on the control mentions one of the probe's `offender_tokens`, the effect is not applied. Add a test, run `make classify`, confirm the row becomes "accepted with warning". No API calls needed.
- [x] **F2. Add effect checks where the response body proves the effect.** `sync.audio.mp3`, `sync.header.model_u36` and `dictation.config.language_string` all returned a correct transcript but are classed "unverifiable" because no check was written. Check that `text` contains the clip's `must_contain` words. Offline.
  - **Done 2026-11-15, narrower than written.** Only `sync.audio.mp3` got a deciding check: a correct transcript of the MP3 is the effect the caller wanted. For `sync.header.model_u36` a correct transcript cannot show which model ran (Sync does not echo it, and a silent model substitution would look identical), and for `dictation.config.language_string` the clip is English, which the default also produces. Both stay "unverifiable" with that reason in `evidence`. A discriminating re-probe for the dictation row (`language_codes: "it"` as a string on `short_it.wav`) can ride along with F5.
  - F1 as written fired on `async.summarization.top_level` too (its deprecation notice names `summarization`, but the summary was returned). The rule therefore also requires the warning to say the input was skipped, ignored or unsupported (`classify.NOT_USED`). Tests cover both cases.
- [ ] **F3. Do not retune the fix tokens after seeing data.** Three opaque rows have messages a reader might call helpful: `dictation.audio.mp3` (points to another endpoint), `async.sentiment.non_english` (names the feature), `async.upload.json_body` (names the file type). The rule was fixed in advance, so keep the class, and say in the write-up's limitations that "names the fix" is a token match and that `matrix.csv` carries the raw message.
- [ ] **F4. Resolve the verbatim clip** as part of Phase 2 question Q3.
- [ ] **F5. Re-check the skill disagreements.** Re-run those five probes (`python -m twohundred probes run --only <ids> --force`) on a later day. Note the account's creation date, since the skill ties model defaults to it.
- [x] **F6. Account identifiers in committed records.** Done 2026-11-16: `scripts/redact_account_ids.py` rewrote every saved record, `_trim_transcript` drops them, `tests/test_account_ids.py` guards it. The public copy is published from a clean history (decision 39). The transcript objects saved under `results/phase1/raw/` include the account's numeric `project_id` and `token_id` and its upload URLs. They are not credentials, but they are in git history. Before the repo is public: drop those keys in `transport._trim_transcript`, rewrite the saved records, and decide with the owner whether to publish from a clean history. See `STATE.md`.

## Changes after the first commit

- `5d1458b` (owner): two Windows fixes. `check_secrets.py` reports paths with forward slashes; `.env` is read as `utf-8-sig` so a byte-order mark does not hide the first variable.
