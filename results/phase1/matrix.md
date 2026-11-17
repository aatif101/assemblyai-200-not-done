# Probe matrix

Generated 2026-11-15T08:17:54Z from raw records in `results/phase1/raw/`. Probe list drawn from AssemblyAI/assemblyai-skill at `f584ec5` (2026-11-04).

Classes: **instructive error** (rejected, and the message names the fix), **opaque error** (rejected, message does not), **silent accept** (accepted, intended effect not applied, no warning anywhere in the response), **accepted with warning**, **accepted and applied**.

The `skill says` column is what AssemblyAI's own skill documents for that input; `agrees` compares it with what the API did.

| Class | Probes |
|---|---|
| instructive error | 17 |
| opaque error | 10 |
| silent accept | 6 |
| accepted with warning | 4 |
| accepted and applied | 10 |
| accepted, effect unverifiable | 4 |

## async

| Probe | Mistake | Skill says | Observed | Response | Evidence | Agrees |
|---|---|---|---|---|---|---|
| `async.auth.bearer` | `Authorization: Bearer KEY` instead of the raw key | unspecified | **accepted and applied** | HTTP 200 | request was authenticated | n/a |
| `async.unknown_field` | a parameter that does not exist (`zz_not_a_real_param`) | unspecified | **opaque error** (names input: no) | HTTP 400 | error: Invalid endpoint schema, please refer to documentation for examples. | n/a |
| `async.word_boost` | `word_boost` (+`boost_param`) instead of `keyterms_prompt` | unspecified | **accepted with warning** | HTTP 200 | a warning names the input as not used (body metadata.warnings: [{"message": "Skipped word_boost because it is not a supported feature on Universal-3 Pro."}]); echo {'word_boost': ['Quorvex', 'Zyntrix Flow'], 'keyterms_p… | n/a |
| `async.speech_model.singular` | singular `speech_model` with a current model name | reject | **instructive error** (names input: yes, names fix: yes) | HTTP 400 | error: The speech_model parameter is deprecated. Use speech_models: ["universal-3-5-pro", "universal-2"] for the best accuracy, performance, and language coverage. See documentation: https://www.assemblyai.com/docs/pre-… | yes |
| `async.speech_models.u3_pro` | `speech_models: ["universal-3-pro"]` (superseded model) | reject | **accepted and applied** | HTTP 200 | requested 'universal-3-pro', speech_model_used='universal-3-pro' | NO |
| `async.speech_models.u36_pro` | streaming-only `universal-3-6-pro` on the async API | reject | **instructive error** (names input: no, names fix: yes) | HTTP 400 | error: "speech_models" must be a non-empty list containing one or more of: "universal-3-5-pro", "universal-2" | yes |
| `async.speech_models.universal` | `speech_models: ["universal"]` (legacy name) | silent | **silent accept** | HTTP 200 | requested 'universal', speech_model_used='universal-2' | yes |
| `async.summarization.top_level` | deprecated top-level `summarization` params | honored | **accepted and applied** | HTTP 200 | summary returned · warning: body metadata.warnings: [{"message": "[Parameter change] Starting September 15, 2026, requests with a \"summarization\", \"summary_model\", or \"summary_type\" parameter specified will return… | yes |
| `async.speech_understanding.no_request` | `speech_understanding.translation` without the `request` wrapper | reject | **instructive error** (names input: yes, names fix: yes) | HTTP 400 | error: speech_understanding.request field not provided | yes |
| `async.domain.medical` | `domain: "medical"` instead of `"medical-v1"` | unspecified | **instructive error** (names input: yes, names fix: yes) | HTTP 400 | error: Unsupported domain. Please try again, using domains from this list: ['medical-v1', 'veterinary-v1']. | n/a |
| `async.domain.unsupported_language` | Medical Mode on a language it does not support (Italian) | warn | **accepted with warning** | HTTP 200 | metadata.domain_used=None · warning: body metadata.warnings: [{"message": "Skipped medical-v1 correction because the language is not supported"}] | yes |
| `async.sentiment.non_english` | `sentiment_analysis` on non-English audio (Italian) | silent | **opaque error** (names input: yes, names fix: no) | HTTP 400 | error: The following models are not available in this language: sentiment_analysis | NO |
| `async.prompt.instructions` | behavioral instructions in `prompt` ("transcribe verbatim, keep um/uh") | silent | **accepted, effect unverifiable** | HTTP 200 | control did not show the effect (5 fillers in transcript, same as or fewer than the 5 with no options); mistake: 5 fillers in transcript, same as or fewer than the 5 with no options | consistent (not verifiable) |
| `async.language_code.hyphen` | `language_code: "en-US"` instead of `"en_us"` | unspecified | **opaque error** (names input: yes, names fix: no) | HTTP 400 | error: Language code en-US is not supported. See https://www.assemblyai.com/docs/concepts/supported-languages. | n/a |
| `async.pii.policy_name` | `redact_pii_policies: ["ssn"]` instead of `us_social_security_number` | unspecified | **instructive error** (names input: yes, names fix: yes) | HTTP 400 | error: Unsupported policy name in 'redact_pii_policies', supported policy names are ['account_number', 'banking_information', 'blood_type', 'credit_card_number', 'credit_card_expiration', 'credit_card_cvv', 'date', 'dat… | n/a |
| `async.lemur` | calling the retired LeMUR endpoint | reject | **opaque error** (names input: no, names fix: no) | HTTP 404 | Not found | yes |
| `async.upload.json_body` | JSON body sent to `/v2/upload` instead of raw audio bytes | late-fail | **opaque error** (names input: yes, names fix: no) | HTTP 200, then status=error | Transcoding failed. File type application/json (JSON text data) may be unsupported or an unexpected error occurred. See supported formats at https://www.assemblyai.com/docs/faq/what-audio-and-video-file-types-are-suppor… | yes |

## streaming

| Probe | Mistake | Skill says | Observed | Response | Evidence | Agrees |
|---|---|---|---|---|---|---|
| `stream.auth.bearer` | `Authorization: Bearer KEY` on the websocket | unspecified | **opaque error** (names input: no, names fix: no) | error frame, close 1008 | type: Error error_code: 1008 error: Unauthorized Connection: Invalid API key See Error message for details | n/a |
| `stream.param.model_name` | `model=` instead of `speech_model=` | silent | **silent accept** | session opened | Begin.configuration.model='universal-3-6-pro' | yes |
| `stream.model.u3_rt_pro` | `speech_model=u3-rt-pro` (removed model) | silent | **silent accept** | session opened | Begin.configuration.model='universal-3-6-pro' | yes |
| `stream.model.universal_3_6` | `speech_model=universal-3-6` (SDK enum value, not a documented model) | unspecified | **accepted and applied** | session opened | Begin.configuration.model='universal-3-6' | n/a |
| `stream.model.invalid` | `speech_model=whisper-large-v3` (not an AssemblyAI model) | unspecified | **instructive error** (names input: yes) | error frame, close 3006 | type: Error error_code: 3006 error: User Input Validation Error: Invalid 'speech_model': Input should be 'universal-streaming-english', 'universal-streaming-multilingual', 'whisper-rt', 'u3-rt-pro', 'u3-rt-agent', 'univ… | n/a |
| `stream.param.word_boost` | `word_boost=[...]` instead of `keyterms_prompt=[...]` | silent | **silent accept** | session opened | not part of the public API (no server-side echo for this input) | yes |
| `stream.param.camel_case` | `speakerLabels=true` instead of `speaker_labels=true` | silent | **silent accept** | session opened | Begin.configuration.speaker_labels=False | yes |
| `stream.value.mode` | `mode=max-accuracy` (hyphen) instead of `max_accuracy` | unspecified | **instructive error** (names input: yes, names fix: yes) | error frame, close 3006 | type: Error error_code: 3006 error: User Input Validation Error: Invalid 'mode': Input should be 'max_accuracy', 'min_latency' or 'balanced' See Error message for details | n/a |
| `stream.value.domain` | `domain=medical` instead of `medical-v1` | unspecified | **accepted and applied** | session opened | Begin.configuration.domain='medical-v1' | n/a |
| `stream.param.v2_silence` | v2-era `end_utterance_silence_threshold=1000` | silent | **silent accept** | session opened | not part of the public API (no server-side echo for this input) | yes |
| `stream.keyterms.csv` | `keyterms_prompt` as a comma string instead of a JSON array | unspecified | **instructive error** (names input: yes, names fix: yes) | error frame, close 3006 | type: Error error_code: 3006 error: User Input Validation Error: Invalid 'keyterms_prompt': Value error, Invalid JSON array See Error message for details | n/a |
| `stream.pacing.fast` | replaying a file without pacing (12 s of audio sent at once) | reject | **accepted and applied** | session opened | server accepted unpaced audio | NO |
| `stream.url.v2` | hardcoded v2 realtime URL | honored | **opaque error** (names input: no, names fix: no) | HTTP 404 | Not found | NO |

## sync

| Probe | Mistake | Skill says | Observed | Response | Evidence | Agrees |
|---|---|---|---|---|---|---|
| `sync.config.unknown_field` | async-only `speaker_labels` in the Sync `config` | reject | **instructive error** (names input: yes) | HTTP 400 | status: 400 title: Bad Request detail: invalid config part: speaker_labels: Extra inputs are not permitted | yes |
| `sync.header.missing_model` | no `X-AAI-Model` header | unspecified | **opaque error** (names input: no, names fix: no) | HTTP 404 | Not found | n/a |
| `sync.header.model_u36` | `X-AAI-Model: universal-3-6-pro` (streaming-only model) | unspecified | **accepted, effect unverifiable** | HTTP 200 | correct transcript returned, but Sync responses do not echo the model, so which model ran is not visible | n/a |
| `sync.config.word_boost` | `word_boost` alias in the Sync `config` | honored | **accepted, effect unverifiable** | HTTP 200 | Sync responses do not echo the config | consistent (not verifiable) |
| `sync.audio.mp3` | MP3 audio | reject | **accepted and applied** | HTTP 200 | transcript contains ['quarterly', 'Tuesday'] | NO |
| `sync.auth.bearer` | `Authorization: Bearer KEY` | honored | **accepted and applied** | HTTP 200 | request was authenticated | yes |

## dictation

| Probe | Mistake | Skill says | Observed | Response | Evidence | Agrees |
|---|---|---|---|---|---|---|
| `dictation.config.unknown_field` | async-only `redact_pii` in the Dictation `config` | reject | **instructive error** (names input: yes) | HTTP 400 | status: 400 title: Bad Request detail: invalid config part: redact_pii: Extra inputs are not permitted | yes |
| `dictation.config.model` | `model` in the Dictation `config` | reject | **instructive error** (names input: yes) | HTTP 400 | status: 400 title: Bad Request detail: invalid config part: model: Extra inputs are not permitted | yes |
| `dictation.multipart.audio_first` | `audio` part sent before `config` | reject | **instructive error** (names input: yes, names fix: yes) | HTTP 400 | error: the `config` part must be sent before the `audio` part on the streaming endpoint, because the upstream call cannot be opened without it error_code: bad_request | yes |
| `dictation.multipart.no_config` | no `config` part at all | reject | **instructive error** (names input: no, names fix: yes) | HTTP 400 | error: the `config` part must be sent before the `audio` part on the streaming endpoint, because the upstream call cannot be opened without it error_code: bad_request | yes |
| `dictation.config.language_string` | `language_codes: "en"` (string, not a list) | unspecified | **accepted, effect unverifiable** | HTTP 200 | correct transcript returned, but the clip is English, which the default also produces, so whether the string was read as a language hint is not visible | n/a |
| `dictation.auth.bearer` | `Authorization: Bearer KEY` | unspecified | **accepted and applied** | HTTP 200 | request was authenticated | n/a |
| `dictation.audio.mp3` | MP3 audio | reject | **opaque error** (names input: yes, names fix: no) | HTTP 415 | status: 415 title: Unsupported Media Type detail: 'audio/mpeg' cannot be decoded incrementally; send it to the buffered /v1/transcribe endpoint instead | yes |

## tts

| Probe | Mistake | Skill says | Observed | Response | Evidence | Agrees |
|---|---|---|---|---|---|---|
| `tts.voice_param` | `voice` instead of `voice_id` | reject | **instructive error** (names input: yes, names fix: yes) | HTTP 422 | code: invalid_parameter message: voice: voice is retired: name the voice with voice_id, for example voice_id=aai-jane param: voice request_id: 9a71c282-5237-4f86-986d-ce2968cd17fb errors: message: voice is retired: name… | yes |
| `tts.voice_bare_name` | bare voice name (`voice_id: "jane"`) | reject | **instructive error** (names input: yes, names fix: yes) | HTTP 422 | code: voice_not_found message: The requested voice was not found. It may not exist, may have been deleted, or may be another account's private voice. Voices exist only in the data zone where they were created, so it may… | yes |
| `tts.unknown_field` | a parameter that does not exist (`zz_not_a_real_param`) | warn | **accepted with warning** | HTTP 200 | not part of the public API (no server-side echo for this input) · warning: header x-tts-unknown-params: zz_not_a_real_param | yes |
| `tts.response_format` | OpenAI-style `response_format: "wav"` instead of `container: "wav"` | warn | **accepted with warning** | HTTP 200 | body is not WAV (first bytes '35 00 a2 00 2d 01 85 01 79 01 6d 01') · warning: header x-tts-unknown-params: response_format | yes |
| `tts.wav_with_mp3` | `container: "wav"` together with `encoding: "mp3"` | reject | **instructive error** (names input: yes, names fix: yes) | HTTP 422 | code: invalid_parameter message: container: wav needs a pcm encoding; mp3 has no container param: container request_id: 8b766a02-f1ad-41bc-831d-cad4a78429c3 errors: message: wav needs a pcm encoding; mp3 has no containe… | yes |
| `tts.wrong_host` | TTS request sent to `api.assemblyai.com` | reject | **opaque error** (names input: no, names fix: no) | HTTP 404 | Not found | yes |
| `tts.auth.bearer` | `Authorization: Bearer KEY` | unspecified | **accepted and applied** | HTTP 200 | request was authenticated | n/a |
| `tts.speed` | `speed: 1.25` | reject | **instructive error** (names input: yes) | HTTP 422 | code: invalid_parameter message: speed: only 1 is supported, got 1.25 param: speed request_id: ba0c3721-7dce-49ef-9ca7-d510fc9efea2 errors: message: only 1 is supported, got 1.25 param: speed retryable: False retry_afte… | yes |
