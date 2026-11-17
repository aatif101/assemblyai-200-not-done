# Probe matrix (kill test)

Generated 2026-11-14T21:55:24Z from raw records in `results/phase1/raw/`. Probe list drawn from AssemblyAI/assemblyai-skill at `f584ec5` (2026-11-04).

Classes: **instructive error** (rejected, and the message names the fix), **opaque error** (rejected, message does not), **silent accept** (accepted, intended effect not applied, no warning anywhere in the response), **accepted with warning**, **accepted and applied**.

The `skill says` column is what AssemblyAI's own skill documents for that input; `agrees` compares it with what the API did.

| Class | Probes |
|---|---|
| instructive error | 3 |
| opaque error | 1 |
| silent accept | 2 |
| accepted with warning | 1 |
| accepted and applied | 3 |

**Kill test: 4 of 10 rejected loudly (threshold for pivot: 8). PROCEED: enough requests are accepted without an error to measure.**

## async

| Probe | Mistake | Skill says | Observed | Response | Evidence | Agrees |
|---|---|---|---|---|---|---|
| `async.auth.bearer` | `Authorization: Bearer KEY` instead of the raw key | unspecified | **accepted and applied** | HTTP 200 | request was authenticated | n/a |
| `async.unknown_field` | a parameter that does not exist (`zz_not_a_real_param`) | unspecified | **opaque error** (names input: no) | HTTP 400 | error: Invalid endpoint schema, please refer to documentation for examples. | n/a |
| `async.word_boost` | `word_boost` (+`boost_param`) instead of `keyterms_prompt` | unspecified | **accepted and applied** | HTTP 200 | echo {'word_boost': ['Quorvex', 'Zyntrix Flow'], 'keyterms_prompt': []} (echo only; decoder bias not measured) · warning: body metadata.warnings: [{"message": "Skipped word_boost because it is not a supported feature on… | n/a |
| `async.speech_models.u3_pro` | `speech_models: ["universal-3-pro"]` (superseded model) | reject | **accepted and applied** | HTTP 200 | requested 'universal-3-pro', speech_model_used='universal-3-pro' | NO |

## streaming

| Probe | Mistake | Skill says | Observed | Response | Evidence | Agrees |
|---|---|---|---|---|---|---|
| `stream.param.model_name` | `model=` instead of `speech_model=` | silent | **silent accept** | session opened | Begin.configuration.model='universal-3-6-pro' | yes |
| `stream.model.u3_rt_pro` | `speech_model=u3-rt-pro` (removed model) | silent | **silent accept** | session opened | Begin.configuration.model='universal-3-6-pro' | yes |

## sync

| Probe | Mistake | Skill says | Observed | Response | Evidence | Agrees |
|---|---|---|---|---|---|---|
| `sync.config.unknown_field` | async-only `speaker_labels` in the Sync `config` | reject | **instructive error** (names input: yes) | HTTP 400 | status: 400 title: Bad Request detail: invalid config part: speaker_labels: Extra inputs are not permitted | yes |

## dictation

| Probe | Mistake | Skill says | Observed | Response | Evidence | Agrees |
|---|---|---|---|---|---|---|
| `dictation.config.unknown_field` | async-only `redact_pii` in the Dictation `config` | reject | **instructive error** (names input: yes) | HTTP 400 | status: 400 title: Bad Request detail: invalid config part: redact_pii: Extra inputs are not permitted | yes |

## tts

| Probe | Mistake | Skill says | Observed | Response | Evidence | Agrees |
|---|---|---|---|---|---|---|
| `tts.voice_param` | `voice` instead of `voice_id` | reject | **instructive error** (names input: yes, names fix: yes) | HTTP 422 | code: invalid_parameter message: voice: voice is retired: name the voice with voice_id, for example voice_id=aai-jane param: voice request_id: 9a71c282-5237-4f86-986d-ce2968cd17fb errors: message: voice is retired: name… | yes |
| `tts.unknown_field` | a parameter that does not exist (`zz_not_a_real_param`) | warn | **accepted with warning** | HTTP 200 | not part of the public API (no server-side echo for this input) · warning: header x-tts-unknown-params: zz_not_a_real_param | yes |
