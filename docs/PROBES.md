# Pre-registered probe list

51 known-wrong requests, written before any was sent. Each is paired with a control: the same request with the mistake corrected. A probe counts only if its control succeeds.

Source for `skill` probes: AssemblyAI/assemblyai-skill at `f584ec557e97ca9ce8151ec4d3b6e2ec8e3ade75` (2026-11-04). The quoted sentence is what that revision says about the input. `author` probes are generic mistakes added here and are not attributed to the skill.

`Skill says` is the behavior the skill documents: reject, silent (ignored or redirected without an error), warn (accepted, with a warning channel), honored (still works), late-fail, or unspecified (listed as a mistake without saying how the API responds).

Kill test probes are marked K.

## How a probe is judged

Decided mechanically by `twohundred/classify.py` from the saved records, never by reading and deciding:

| Observation | Class |
|---|---|
| Rejected (HTTP 4xx, refused or error-closed websocket, transcript `status: error`) and the error text contains one of the probe's names-the-fix tokens | instructive error |
| Rejected, no such token | opaque error |
| Accepted, server-side evidence shows the intended effect was not applied, and no warning in any header, body field or frame that the control did not also carry | silent accept |
| Accepted, effect not applied (or not verifiable), with such a warning | accepted with warning |
| Accepted and the effect is verified applied | accepted and applied |
| Accepted, nothing in the response can confirm or refute the effect | accepted, effect unverifiable |
| The control request failed | invalid (says nothing about the API) |
| 5xx, timeout, transport failure | inconclusive |

Where a probe has no names-the-fix tokens (the input simply does not exist), an error that names the offending input counts as instructive. Server-side evidence means: the config echoed by `GET /v2/transcript/{id}`, `speech_model_used`, `metadata`, the streaming `Begin.configuration`, or the bytes returned. A parameter name that is not part of the public API is treated as not applied by construction.

Kill-test rule, fixed in advance: if 8 or more of the 10 kill probes are rejected loudly, the premise fails and the study pivots to ranking error messages.

## async

| | Probe | Mistake | Origin | Skill says | Names-the-fix tokens | Skill text |
|---|---|---|---|---|---|---|
| K | `async.auth.bearer` | `Authorization: Bearer KEY` instead of the raw key | skill | unspecified | bearer | SKILL.md: “just the raw API key, no Bearer prefix” |
| K | `async.unknown_field` | a parameter that does not exist (`zz_not_a_real_param`) | author | unspecified | (names the input) |  |
| K | `async.word_boost` | `word_boost` (+`boost_param`) instead of `keyterms_prompt` | skill | unspecified | keyterms_prompt | SKILL.md: “\| `word_boost` anywhere \| Use `keyterms_prompt` instead” |
|  | `async.speech_model.singular` | singular `speech_model` with a current model name | skill | reject | speech_models | SKILL.md: “The **singular** `speech_model` field rejects current model names with a 400” |
| K | `async.speech_models.u3_pro` | `speech_models: ["universal-3-pro"]` (superseded model) | skill | reject | universal-3-5-pro | SKILL.md: “`speech_models: ["universal-3-pro"]` returns an **error**” |
|  | `async.speech_models.u36_pro` | streaming-only `universal-3-6-pro` on the async API | skill | reject | universal-3-5-pro | SKILL.md: “`universal-3-6-pro` is not in the `POST /v2/transcript` `speech_models` enum” |
|  | `async.speech_models.universal` | `speech_models: ["universal"]` (legacy name) | skill | silent | universal-2, universal-3-5-pro | SKILL.md: “or `["universal"]`, routes to `universal-3-5-pro`” |
|  | `async.summarization.top_level` | deprecated top-level `summarization` params | skill | honored | speech_understanding, gateway | references/audio-intelligence.md: “Legacy parameters (still functional but not recommended for new code)” |
|  | `async.speech_understanding.no_request` | `speech_understanding.translation` without the `request` wrapper | skill | reject | speech_understanding.request, "request", 'request', `request`, request key, request field, request object, request wrapper, missing request | SKILL.md: “`speech_understanding.translation` (no `.request`) is invalid” |
|  | `async.domain.medical` | `domain: "medical"` instead of `"medical-v1"` | skill | unspecified | medical-v1 | SKILL.md: “\| Medical Mode `domain: "medical"` \| Correct value is `domain: "medical-v1"` \|” |
|  | `async.domain.unsupported_language` | Medical Mode on a language it does not support (Italian) | skill | warn | english, spanish, german, french, supported | SKILL.md: “If used with an unsupported language, the API ignores `domain` and returns a warning” |
|  | `async.sentiment.non_english` | `sentiment_analysis` on non-English audio (Italian) | skill | silent | english | SKILL.md: “For non-English audio the transcript completes normally but `sentiment_analysis_results` is **`null`**” |
|  | `async.prompt.instructions` | behavioral instructions in `prompt` ("transcribe verbatim, keep um/uh") | skill | silent | disfluencies | SKILL.md: “are **not supported** and are ignored” |
|  | `async.language_code.hyphen` | `language_code: "en-US"` instead of `"en_us"` | author | unspecified | en_us |  |
|  | `async.pii.policy_name` | `redact_pii_policies: ["ssn"]` instead of `us_social_security_number` | author | unspecified | us_social_security_number |  |
|  | `async.lemur` | calling the retired LeMUR endpoint | skill | reject | gateway | SKILL.md: “the LeMUR endpoints answer 404” |
|  | `async.upload.json_body` | JSON body sent to `/v2/upload` instead of raw audio bytes | skill | late-fail | binary, raw bytes, octet-stream | SKILL.md: “`-d`/JSON returns a valid `upload_url` but transcription later fails with” |

## streaming

| | Probe | Mistake | Origin | Skill says | Names-the-fix tokens | Skill text |
|---|---|---|---|---|---|---|
|  | `stream.auth.bearer` | `Authorization: Bearer KEY` on the websocket | skill | unspecified | bearer | references/streaming.md: “(raw key, no `Bearer`)” |
| K | `stream.param.model_name` | `model=` instead of `speech_model=` | skill | silent | speech_model | SKILL.md: “Unknown or misspelled query params are ignored, not rejected” |
| K | `stream.model.u3_rt_pro` | `speech_model=u3-rt-pro` (removed model) | skill | silent | universal-3-6-pro, universal-3-5-pro | SKILL.md: “`u3-rt-pro` connections are silently redirected to `universal-3-5-pro`” |
|  | `stream.model.universal_3_6` | `speech_model=universal-3-6` (SDK enum value, not a documented model) | skill | unspecified | universal-3-6-pro | SKILL.md: “Both enums also list a value named `universal-3-6` (no `-pro`)” |
|  | `stream.model.invalid` | `speech_model=whisper-large-v3` (not an AssemblyAI model) | author | unspecified | (names the input) |  |
|  | `stream.param.word_boost` | `word_boost=[...]` instead of `keyterms_prompt=[...]` | skill | silent | keyterms_prompt | SKILL.md: “Unknown or misspelled query params are ignored, not rejected” |
|  | `stream.param.camel_case` | `speakerLabels=true` instead of `speaker_labels=true` | author | silent | speaker_labels | SKILL.md: “Unknown or misspelled query params are ignored, not rejected” |
|  | `stream.value.mode` | `mode=max-accuracy` (hyphen) instead of `max_accuracy` | author | unspecified | max_accuracy |  |
|  | `stream.value.domain` | `domain=medical` instead of `medical-v1` | skill | unspecified | medical-v1 | SKILL.md: “\| Medical Mode `domain: "medical"` \| Correct value is `domain: "medical-v1"` \|” |
|  | `stream.param.v2_silence` | v2-era `end_utterance_silence_threshold=1000` | author | silent | min_turn_silence, max_turn_silence | SKILL.md: “Unknown or misspelled query params are ignored, not rejected” |
|  | `stream.keyterms.csv` | `keyterms_prompt` as a comma string instead of a JSON array | skill | unspecified | json, array | references/streaming.md: “must be JSON.stringify'd” |
|  | `stream.pacing.fast` | replaying a file without pacing (12 s of audio sent at once) | skill | reject | real-time, realtime, real time, pace, faster | references/streaming.md: “audio was sent faster than real-time” |
|  | `stream.url.v2` | hardcoded v2 realtime URL | skill | honored | v3, streaming.assemblyai.com | SKILL.md: “\| Hardcoding v2 streaming URL \| v3 (`/v3/ws`) is current; v2 still works but is legacy \|” |

## sync

| | Probe | Mistake | Origin | Skill says | Names-the-fix tokens | Skill text |
|---|---|---|---|---|---|---|
| K | `sync.config.unknown_field` | async-only `speaker_labels` in the Sync `config` | skill | reject | (names the input) | SKILL.md: “**Unknown `config` fields are rejected with a 400**” |
|  | `sync.header.missing_model` | no `X-AAI-Model` header | skill | unspecified | x-aai-model | SKILL.md: “**Required header:** `X-AAI-Model: universal-3-5-pro`” |
|  | `sync.header.model_u36` | `X-AAI-Model: universal-3-6-pro` (streaming-only model) | skill | unspecified | universal-3-5-pro, u3-sync-pro | SKILL.md: “Use `universal-3-5-pro` in async `speech_models` and the Sync `X-AAI-Model` header” |
|  | `sync.config.word_boost` | `word_boost` alias in the Sync `config` | skill | honored | keyterms_prompt | SKILL.md: “the aliases `word_boost` and `keyterms` are still accepted” |
|  | `sync.audio.mp3` | MP3 audio | skill | reject | wav, pcm | references/api-reference.md: “\| 415 \| `unsupported_media_type` \| Unsupported format” |
|  | `sync.auth.bearer` | `Authorization: Bearer KEY` | skill | honored | bearer | SKILL.md: “Bearer prefix optional here, unlike the async REST API” |

## dictation

| | Probe | Mistake | Origin | Skill says | Names-the-fix tokens | Skill text |
|---|---|---|---|---|---|---|
| K | `dictation.config.unknown_field` | async-only `redact_pii` in the Dictation `config` | skill | reject | (names the input) | SKILL.md: “unknown → `400`” |
|  | `dictation.config.model` | `model` in the Dictation `config` | skill | reject | (names the input) | SKILL.md: “Dictation takes no model at all” |
|  | `dictation.multipart.audio_first` | `audio` part sent before `config` | skill | reject | first, before, order | SKILL.md: “must be the **first** multipart part” |
|  | `dictation.multipart.no_config` | no `config` part at all | skill | reject | config | SKILL.md: “audio-before-config or a missing config is a `400`” |
|  | `dictation.config.language_string` | `language_codes: "en"` (string, not a list) | skill | unspecified | list, array | SKILL.md: “It's always a **list**: `language_codes: ["es"]`” |
|  | `dictation.auth.bearer` | `Authorization: Bearer KEY` | skill | unspecified | bearer | SKILL.md: “**Auth:** `Authorization: YOUR_API_KEY` (raw key, no Bearer)” |
|  | `dictation.audio.mp3` | MP3 audio | skill | reject | wav, pcm | SKILL.md: “**Compressed formats (MP3/M4A/FLAC/OGG/WebM) → `415`**” |

## tts

| | Probe | Mistake | Origin | Skill says | Names-the-fix tokens | Skill text |
|---|---|---|---|---|---|---|
| K | `tts.voice_param` | `voice` instead of `voice_id` | skill | reject | voice_id | SKILL.md: “\| TTS `voice: "aai-kyla"` \| Rejected with validation error” |
|  | `tts.voice_bare_name` | bare voice name (`voice_id: "jane"`) | skill | reject | aai- | SKILL.md: “`voice_not_found` — TTS takes the library slug, `aai-jane`” |
| K | `tts.unknown_field` | a parameter that does not exist (`zz_not_a_real_param`) | skill | warn | (names the input) | references/text-to-speech.md: “Unknown fields are ignored and listed in `X-TTS-Unknown-Params`” |
|  | `tts.response_format` | OpenAI-style `response_format: "wav"` instead of `container: "wav"` | author | warn | container | references/text-to-speech.md: “Unknown fields are ignored and listed in `X-TTS-Unknown-Params`” |
|  | `tts.wav_with_mp3` | `container: "wav"` together with `encoding: "mp3"` | skill | reject | pcm | SKILL.md: “\| Setting `container="wav"` with `encoding="mp3"` \| Rejected with validation error” |
|  | `tts.wrong_host` | TTS request sent to `api.assemblyai.com` | skill | reject | tts. | SKILL.md: “\| Calling `https://api.assemblyai.com` for TTS \| 404” |
|  | `tts.auth.bearer` | `Authorization: Bearer KEY` | author | unspecified | bearer |  |
|  | `tts.speed` | `speed: 1.25` | skill | reject | (names the input) | references/text-to-speech.md: “Only `1` is accepted: there is no speed control” |
