"""The probe list: known-wrong requests, each paired with its corrected control.

A probe is a mistake a coding agent plausibly makes when writing AssemblyAI code.
Most are taken from the mistakes AssemblyAI's own skill warns about (origin="skill",
with the sentence quoted); a few generic ones are added and labelled origin="author".

Nothing here decides an outcome. `build(ctx, correct)` returns the request to send;
`effect(rec, aux)` reads server-side evidence out of a saved record. Classification
lives in classify.py and runs offline from results/phase1/raw.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

from .config import ENDPOINTS
from .transport import Part, Req

APPLIED, NOT_APPLIED, UNVERIFIABLE = "applied", "not_applied", "unverifiable"
Effect = tuple[str, str]  # (APPLIED | NOT_APPLIED | UNVERIFIABLE, evidence)

TERMS = ["Quorvex", "Zyntrix Flow"]
EN, IT, CALL, VERBATIM, MP3 = (
    "short_en.wav", "short_it.wav", "support_call.wav", "verbatim_memo.wav", "short_en.mp3",
)


class Ctx:
    """What a probe needs at build time: uploaded audio URLs and one transcript id."""

    def __init__(self, upload: Callable[[str], str], transcript_id: Callable[[], str]):
        self._upload, self._tid = upload, transcript_id
        self._cache: dict[str, str] = {}

    def audio_url(self, fixture: str) -> str:
        if fixture not in self._cache:
            self._cache[fixture] = self._upload(fixture)
        return self._cache[fixture]

    def transcript_id(self) -> str:
        return self._tid()


@dataclass
class Probe:
    id: str
    surface: str                  # async | streaming | sync | dictation | tts
    mistake: str                  # what the caller got wrong, in one line
    build: Callable[[Ctx, bool], Req | None]   # correct=True -> the control request
    documented: str               # reject | silent | warn | honored | late-fail | unspecified
    origin: str = "skill"         # skill | author
    source_file: str | None = None
    source_quote: str | None = None   # exact substring of source_file at SKILL_COMMIT
    fix_tokens: list[str] = field(default_factory=list)       # naming any of these = error names the fix
    offender_tokens: list[str] = field(default_factory=list)  # naming any of these = error names the bad input
    effect: Callable[[dict, dict], Effect] | None = None
    # Run on the *control* record. If the control does not show the effect, the effect
    # check cannot tell applied from not-applied and the mistake is reported unverifiable.
    control_effect: Callable[[dict, dict], Effect] | None = None
    by_construction: bool = False  # the name/value is not part of the public API, so it cannot have applied
    kill: bool = False             # part of the 10-probe kill test
    fixtures: list[str] = field(default_factory=lambda: [EN])
    note: str = ""


# ---------------------------------------------------------------- evidence readers


def final(rec: dict) -> dict:
    """The terminal transcript object for async probes (falls back to the POST body)."""
    return rec.get("final") or (rec.get("response") or {}).get("json") or {}


def begin_cfg(rec: dict) -> dict:
    return ((rec.get("ws") or {}).get("begin") or {}).get("configuration") or {}


def _echo(key: str, want: Any) -> Callable[[dict, dict], Effect]:
    def check(rec: dict, aux: dict) -> Effect:
        t = final(rec)
        if key not in t:
            return UNVERIFIABLE, f"transcript has no `{key}` field"
        return (APPLIED if t[key] == want else NOT_APPLIED), f"{key}={t[key]!r}"
    return check


def _model_used(want: str) -> Callable[[dict, dict], Effect]:
    def check(rec: dict, aux: dict) -> Effect:
        t = final(rec)
        if "speech_model_used" not in t:
            return UNVERIFIABLE, "transcript has no `speech_model_used` field"
        got = t["speech_model_used"]
        return (APPLIED if got == want else NOT_APPLIED), f"requested {want!r}, speech_model_used={got!r}"
    return check


def _begin(key: str, want: Any) -> Callable[[dict, dict], Effect]:
    def check(rec: dict, aux: dict) -> Effect:
        cfg = begin_cfg(rec)
        if not cfg:
            return UNVERIFIABLE, "no Begin.configuration received"
        if key not in cfg:
            return UNVERIFIABLE, f"Begin.configuration has no `{key}`"
        return (APPLIED if cfg[key] == want else NOT_APPLIED), f"Begin.configuration.{key}={cfg[key]!r}"
    return check


def _accepted_is_applied(what: str) -> Callable[[dict, dict], Effect]:
    """For probes where being served at all is the effect (auth scheme, legacy URL)."""
    return lambda rec, aux: (APPLIED, what)


def _no_echo(what: str) -> Callable[[dict, dict], Effect]:
    return lambda rec, aux: (UNVERIFIABLE, what)


FILLER = re.compile(r"\b(um+|uh+|uhm+|erm+|hmm+)\b", re.I)


def filler_count(text: str | None) -> int:
    return len(FILLER.findall(text or ""))


def _word_boost_effect(rec: dict, aux: dict) -> Effect:
    t = final(rec)
    seen = {k: t[k] for k in ("word_boost", "keyterms_prompt") if k in t}
    if not seen:
        return UNVERIFIABLE, "transcript echoes neither `word_boost` nor `keyterms_prompt`"
    hit = any(isinstance(v, list) and set(TERMS) <= set(v) for v in seen.values())
    return (APPLIED if hit else NOT_APPLIED), f"echo {seen!r} (echo only; decoder bias not measured)"


def _summary_effect(rec: dict, aux: dict) -> Effect:
    t = final(rec)
    return (APPLIED, "summary returned") if t.get("summary") else (NOT_APPLIED, f"summary={t.get('summary')!r}")


def _translation_effect(rec: dict, aux: dict) -> Effect:
    t = final(rec)
    resp = ((t.get("speech_understanding") or {}).get("response") or {}).get("translation")
    if resp or t.get("translated_texts"):
        return APPLIED, "translation present in response"
    return NOT_APPLIED, "no translation in response"


def _domain_effect(rec: dict, aux: dict) -> Effect:
    meta = final(rec).get("metadata")
    used = (meta or {}).get("domain_used")
    if used == "medical-v1":
        return APPLIED, "metadata.domain_used='medical-v1'"
    return NOT_APPLIED, f"metadata.domain_used={used!r}" if meta is not None else "no `metadata` object"


def _sentiment_effect(rec: dict, aux: dict) -> Effect:
    res = final(rec).get("sentiment_analysis_results")
    if isinstance(res, list) and res:
        return APPLIED, f"{len(res)} sentiment results"
    return NOT_APPLIED, f"sentiment_analysis_results={res!r}"


def _verbatim_effect(rec: dict, aux: dict) -> Effect:
    got = filler_count(final(rec).get("text"))
    base = filler_count(final(aux.get("baseline", {}).get(VERBATIM, {})).get("text"))
    if got > base:
        return APPLIED, f"{got} fillers in transcript vs {base} with no options"
    return NOT_APPLIED, f"{got} fillers in transcript, same as or fewer than the {base} with no options"


def _lang_effect(rec: dict, aux: dict) -> Effect:
    code = final(rec).get("language_code")
    if code is None:
        return UNVERIFIABLE, "no language_code in transcript"
    return (APPLIED if str(code).lower().startswith("en") else NOT_APPLIED), f"language_code={code!r}"


def _policy_effect(rec: dict, aux: dict) -> Effect:
    pol = final(rec).get("redact_pii_policies")
    if pol is None:
        return UNVERIFIABLE, "transcript has no `redact_pii_policies` field"
    ok = "us_social_security_number" in pol
    return (APPLIED if ok else NOT_APPLIED), f"redact_pii_policies={pol!r}"


def _riff_effect(rec: dict, aux: dict) -> Effect:
    r = rec.get("response") or {}
    if "starts_with_riff" not in r:
        return UNVERIFIABLE, "no audio body recorded"
    return (APPLIED, "body starts with RIFF (WAV)") if r["starts_with_riff"] else (
        NOT_APPLIED, f"body is not WAV (first bytes {r.get('bytes_head_hex')!r})")


EN_MUST_CONTAIN = ["quarterly", "Tuesday"]  # fixtures/scripts/short_en.json ground_truth.must_contain


def _body_text(rec: dict) -> str | None:
    body = (rec.get("response") or {}).get("json")
    return body.get("text") if isinstance(body, dict) else None


def _transcribed(words: list[str]) -> Callable[[dict, dict], Effect]:
    """The effect is a correct transcript of the clip: the returned text has every ground-truth word."""
    def check(rec: dict, aux: dict) -> Effect:
        text = _body_text(rec)
        if text is None:
            return UNVERIFIABLE, "response body has no `text`"
        missing = [w for w in words if w.lower() not in text.lower()]
        if missing:
            return NOT_APPLIED, f"transcript is missing {missing}"
        return APPLIED, f"transcript contains {words}"
    return check


def _served_not_discriminating(what: str) -> Callable[[dict, dict], Effect]:
    """A correct transcript came back, but it would look the same whether or not the setting applied."""
    def check(rec: dict, aux: dict) -> Effect:
        text = _body_text(rec)
        if text is None:
            return UNVERIFIABLE, "response body has no `text`"
        ok = all(w.lower() in text.lower() for w in EN_MUST_CONTAIN)
        return UNVERIFIABLE, (f"correct transcript returned, but {what}" if ok else
                              f"transcript returned without {EN_MUST_CONTAIN}; {what}")
    return check


def _v2_effect(rec: dict, aux: dict) -> Effect:
    msgs = (rec.get("ws") or {}).get("messages") or []
    if any(isinstance(m, dict) and m.get("message_type") == "SessionBegins" for m in msgs):
        return APPLIED, "v2 SessionBegins received"
    return NOT_APPLIED, "no v2 SessionBegins message"


# ---------------------------------------------------------------- request builders

A = ENDPOINTS["async"]
STREAM_Q = {"sample_rate": "16000", "encoding": "pcm_s16le"}


def _transcript(body_wrong: dict, body_right: dict, fixture: str = EN, auth_wrong: str = "raw"):
    def build(ctx: Ctx, correct: bool) -> Req:
        body = {"audio_url": ctx.audio_url(fixture), **(body_right if correct else body_wrong)}
        return Req("http", f"{A}/v2/transcript", json=body, poll=True, auth="raw" if correct else auth_wrong)
    return build


def _transcript2(wrong: tuple[str, dict], right: tuple[str, dict]):
    """Like _transcript, but the mistake and the control use different fixtures."""
    def build(ctx: Ctx, correct: bool) -> Req:
        fixture, body = right if correct else wrong
        return Req("http", f"{A}/v2/transcript", json={"audio_url": ctx.audio_url(fixture), **body}, poll=True)
    return build


def _stream(q_wrong: dict, q_right: dict, auth_wrong: str = "raw"):
    def build(ctx: Ctx, correct: bool) -> Req:
        return Req("ws", ENDPOINTS["streaming"], query={**STREAM_Q, **(q_right if correct else q_wrong)},
                   audio=EN, send_seconds=3.0, auth="raw" if correct else auth_wrong)
    return build


def _sync(cfg_wrong: dict | None, cfg_right: dict | None, *, model_wrong: str | None = "universal-3-5-pro",
          audio_wrong: tuple[str, str] = (EN, "audio/wav"), auth_wrong: str = "raw"):
    def build(ctx: Ctx, correct: bool) -> Req:
        cfg = cfg_right if correct else cfg_wrong
        model = "universal-3-5-pro" if correct else model_wrong
        audio, ctype = (EN, "audio/wav") if correct else audio_wrong
        parts = ([Part("config", "application/json", json=cfg)] if cfg is not None else []) + [
            Part("audio", ctype, audio=audio)]
        return Req("http", f"{ENDPOINTS['sync']}/v1/transcribe", multipart=parts,
                   headers={"X-AAI-Model": model} if model else {},
                   auth="raw" if correct else auth_wrong)
    return build


def _dictation(cfg_wrong: dict | None, *, order_wrong: tuple[str, ...] = ("config", "audio"),
               audio_wrong: tuple[str, str] = (EN, "audio/wav"), auth_wrong: str = "raw"):
    def build(ctx: Ctx, correct: bool) -> Req:
        cfg = {} if correct else cfg_wrong
        audio, ctype = (EN, "audio/wav") if correct else audio_wrong
        order = ("config", "audio") if correct else order_wrong
        by_name = {"config": Part("config", "application/json", json=cfg if cfg is not None else {}),
                   "audio": Part("audio", ctype, audio=audio)}
        return Req("http", f"{ENDPOINTS['dictation']}/v1/transcribe/live",
                   multipart=[by_name[n] for n in order], auth="raw" if correct else auth_wrong)
    return build


TTS_OK = {"text": "Testing, one two three.", "voice_id": "aai-jane", "stream": False}


def _tts(body_wrong: dict, body_right: dict | None = None, *, host_wrong: str | None = None,
         auth_wrong: str = "raw"):
    def build(ctx: Ctx, correct: bool) -> Req:
        body = (body_right or TTS_OK) if correct else body_wrong
        host = ENDPOINTS["tts"] if (correct or not host_wrong) else host_wrong
        return Req("http", f"{host}/v1/speech", json=body, expect_binary=True,
                   auth="raw" if correct else auth_wrong)
    return build


def _lemur(ctx: Ctx, correct: bool) -> Req | None:
    if correct:
        return None  # the replacement (LLM Gateway) is not available on every account tier
    return Req("http", f"{A}/lemur/v3/generate/task",
               json={"transcript_ids": [ctx.transcript_id()], "prompt": "Summarize this call.",
                     "final_model": "anthropic/claude-3-5-sonnet"})


def _upload_json(ctx: Ctx, correct: bool) -> Req:
    if correct:
        return Req("http", f"{A}/v2/transcript", json={"audio_url": ctx.audio_url(EN)}, poll=True)
    pre = Req("http", f"{A}/v2/upload", json={"file": "recording.wav"})
    return Req("http", f"{A}/v2/transcript", json={"audio_url": "{{pre.upload_url}}"}, poll=True, pre=pre)


def _stream_fast(ctx: Ctx, correct: bool) -> Req:
    return Req("ws", ENDPOINTS["streaming"], query={**STREAM_Q, "speech_model": "universal-3-6-pro"},
               audio=CALL, send_seconds=3.0 if correct else 12.0, pace=1.0 if correct else 0.0)


def _stream_v2(ctx: Ctx, correct: bool) -> Req:
    if correct:
        return Req("ws", ENDPOINTS["streaming"], query={**STREAM_Q, "speech_model": "universal-3-6-pro"},
                   audio=EN, send_seconds=3.0)
    return Req("ws", ENDPOINTS["streaming_v2"], query={"sample_rate": "16000"}, audio=EN, send_seconds=3.0)


S, SK = "SKILL.md", "skill"
STREAM, APIREF, TTSREF = "references/streaming.md", "references/api-reference.md", "references/text-to-speech.md"
UNKNOWN_Q = "Unknown or misspelled query params are ignored, not rejected"
M36 = {"speech_model": "universal-3-6-pro"}

PROBES: list[Probe] = [
    # ------------------------------------------------------------------ async (POST /v2/transcript)
    Probe("async.auth.bearer", "async", "`Authorization: Bearer KEY` instead of the raw key",
          _transcript({}, {}, auth_wrong="bearer"), "unspecified", SK, S,
          "just the raw API key, no Bearer prefix",
          fix_tokens=["bearer"], effect=_accepted_is_applied("request was authenticated"), kill=True),
    Probe("async.unknown_field", "async", "a parameter that does not exist (`zz_not_a_real_param`)",
          _transcript({"zz_not_a_real_param": True}, {}), "unspecified", "author",
          offender_tokens=["zz_not_a_real_param"], by_construction=True, kill=True,
          note="Baseline for the surface's unknown-field policy."),
    Probe("async.word_boost", "async", "`word_boost` (+`boost_param`) instead of `keyterms_prompt`",
          _transcript({"word_boost": TERMS, "boost_param": "high"}, {"keyterms_prompt": TERMS}),
          "unspecified", SK, S, "| `word_boost` anywhere | Use `keyterms_prompt` instead",
          fix_tokens=["keyterms_prompt"], offender_tokens=["word_boost", "boost_param"],
          effect=_word_boost_effect, kill=True),
    Probe("async.speech_model.singular", "async", "singular `speech_model` with a current model name",
          _transcript({"speech_model": "universal-3-5-pro"}, {"speech_models": ["universal-3-5-pro"]}),
          "reject", SK, S, "The **singular** `speech_model` field rejects current model names with a 400",
          fix_tokens=["speech_models"], offender_tokens=["speech_model"],
          effect=_model_used("universal-3-5-pro")),
    Probe("async.speech_models.u3_pro", "async", "`speech_models: [\"universal-3-pro\"]` (superseded model)",
          _transcript({"speech_models": ["universal-3-pro"]}, {"speech_models": ["universal-3-5-pro"]}),
          "reject", SK, S, '`speech_models: ["universal-3-pro"]` returns an **error**',
          fix_tokens=["universal-3-5-pro"], offender_tokens=["universal-3-pro"],
          effect=_model_used("universal-3-pro"), kill=True),
    Probe("async.speech_models.u36_pro", "async", "streaming-only `universal-3-6-pro` on the async API",
          _transcript({"speech_models": ["universal-3-6-pro"]}, {"speech_models": ["universal-3-5-pro"]}),
          "reject", SK, S, "`universal-3-6-pro` is not in the `POST /v2/transcript` `speech_models` enum",
          fix_tokens=["universal-3-5-pro"], offender_tokens=["universal-3-6-pro"],
          effect=_model_used("universal-3-6-pro")),
    Probe("async.speech_models.universal", "async", "`speech_models: [\"universal\"]` (legacy name)",
          _transcript({"speech_models": ["universal"]}, {"speech_models": ["universal-2"]}),
          "silent", SK, S, 'or `["universal"]`, routes to `universal-3-5-pro`',
          fix_tokens=["universal-2", "universal-3-5-pro"], offender_tokens=["universal"],
          effect=_model_used("universal")),
    Probe("async.summarization.top_level", "async", "deprecated top-level `summarization` params",
          _transcript({"summarization": True, "summary_model": "informative", "summary_type": "bullets"},
                      {}, fixture=CALL),
          "honored", SK, "references/audio-intelligence.md",
          "Legacy parameters (still functional but not recommended for new code)",
          fix_tokens=["speech_understanding", "gateway"], offender_tokens=["summarization"],
          effect=_summary_effect, fixtures=[CALL]),
    Probe("async.speech_understanding.no_request", "async",
          "`speech_understanding.translation` without the `request` wrapper",
          _transcript({"speech_understanding": {"translation": {"target_languages": ["es"]}}},
                      {"speech_understanding": {"request": {"translation": {"target_languages": ["es"]}}}}),
          "reject", SK, S, "`speech_understanding.translation` (no `.request`) is invalid",
          fix_tokens=["speech_understanding.request", '"request"', "'request'", "`request`", "request key",
                      "request field", "request object", "request wrapper", "missing request"],
          offender_tokens=["translation", "speech_understanding"],
          effect=_translation_effect),
    Probe("async.domain.medical", "async", "`domain: \"medical\"` instead of `\"medical-v1\"`",
          _transcript({"domain": "medical"}, {"domain": "medical-v1"}),
          "unspecified", SK, S, '| Medical Mode `domain: "medical"` | Correct value is `domain: "medical-v1"` |',
          fix_tokens=["medical-v1"], offender_tokens=["domain"], effect=_domain_effect),
    Probe("async.domain.unsupported_language", "async", "Medical Mode on a language it does not support (Italian)",
          _transcript2((IT, {"language_code": "it", "domain": "medical-v1"}), (EN, {"domain": "medical-v1"})),
          "warn", SK, S, "If used with an unsupported language, the API ignores `domain` and returns a warning",
          fix_tokens=["english", "spanish", "german", "french", "supported"], offender_tokens=["domain", "medical"],
          effect=_domain_effect, fixtures=[IT, EN]),
    Probe("async.sentiment.non_english", "async", "`sentiment_analysis` on non-English audio (Italian)",
          _transcript2((IT, {"language_code": "it", "sentiment_analysis": True}), (EN, {"sentiment_analysis": True})),
          "silent", SK, S,
          "For non-English audio the transcript completes normally but `sentiment_analysis_results` is **`null`**",
          fix_tokens=["english"], offender_tokens=["sentiment"], effect=_sentiment_effect, fixtures=[IT, EN]),
    Probe("async.prompt.instructions", "async", "behavioral instructions in `prompt` (\"transcribe verbatim, keep um/uh\")",
          _transcript({"prompt": "Transcribe verbatim. Keep every filler word such as um and uh exactly as spoken."},
                      {"disfluencies": True}, fixture=VERBATIM),
          "silent", SK, S, "are **not supported** and are ignored",
          fix_tokens=["disfluencies"], offender_tokens=["prompt"], effect=_verbatim_effect, fixtures=[VERBATIM]),
    Probe("async.language_code.hyphen", "async", "`language_code: \"en-US\"` instead of `\"en_us\"`",
          _transcript({"language_code": "en-US"}, {"language_code": "en_us"}), "unspecified", "author",
          fix_tokens=["en_us"], offender_tokens=["en-US", "language_code"], effect=_lang_effect),
    Probe("async.pii.policy_name", "async", "`redact_pii_policies: [\"ssn\"]` instead of `us_social_security_number`",
          _transcript({"redact_pii": True, "redact_pii_policies": ["ssn"]},
                      {"redact_pii": True, "redact_pii_policies": ["us_social_security_number"]}),
          "unspecified", "author", fix_tokens=["us_social_security_number"],
          offender_tokens=["ssn", "redact_pii_policies"], effect=_policy_effect, by_construction=True),
    Probe("async.lemur", "async", "calling the retired LeMUR endpoint", _lemur, "reject", SK, S,
          "the LeMUR endpoints answer 404", fix_tokens=["gateway"], offender_tokens=["lemur"]),
    Probe("async.upload.json_body", "async", "JSON body sent to `/v2/upload` instead of raw audio bytes",
          _upload_json, "late-fail", SK, S,
          "`-d`/JSON returns a valid `upload_url` but transcription later fails with",
          fix_tokens=["binary", "raw bytes", "octet-stream"], offender_tokens=["application/json", "json"]),

    # ------------------------------------------------------------------ streaming (v3 websocket)
    Probe("stream.auth.bearer", "streaming", "`Authorization: Bearer KEY` on the websocket",
          _stream(M36, M36, auth_wrong="bearer"), "unspecified", SK, STREAM, "(raw key, no `Bearer`)",
          fix_tokens=["bearer"], effect=_accepted_is_applied("session opened")),
    Probe("stream.param.model_name", "streaming", "`model=` instead of `speech_model=`",
          _stream({"model": "universal-streaming-english"}, {"speech_model": "universal-streaming-english"}),
          "silent", SK, S, UNKNOWN_Q, fix_tokens=["speech_model"], offender_tokens=["model"],
          effect=_begin("model", "universal-streaming-english"), kill=True),
    Probe("stream.model.u3_rt_pro", "streaming", "`speech_model=u3-rt-pro` (removed model)",
          _stream({"speech_model": "u3-rt-pro"}, M36), "silent", SK, S,
          "`u3-rt-pro` connections are silently redirected to `universal-3-5-pro`",
          fix_tokens=["universal-3-6-pro", "universal-3-5-pro"], offender_tokens=["u3-rt-pro"],
          effect=_begin("model", "u3-rt-pro"), kill=True),
    Probe("stream.model.universal_3_6", "streaming", "`speech_model=universal-3-6` (SDK enum value, not a documented model)",
          _stream({"speech_model": "universal-3-6"}, M36), "unspecified", SK, S,
          "Both enums also list a value named `universal-3-6` (no `-pro`)",
          fix_tokens=["universal-3-6-pro"], offender_tokens=["universal-3-6"],
          effect=_begin("model", "universal-3-6")),
    Probe("stream.model.invalid", "streaming", "`speech_model=whisper-large-v3` (not an AssemblyAI model)",
          _stream({"speech_model": "whisper-large-v3"}, M36), "unspecified", "author",
          offender_tokens=["whisper-large-v3", "speech_model"],
          effect=_begin("model", "whisper-large-v3"), by_construction=True),
    Probe("stream.param.word_boost", "streaming", "`word_boost=[...]` instead of `keyterms_prompt=[...]`",
          _stream({**M36, "word_boost": '["Quorvex","Zyntrix Flow"]'},
                  {**M36, "keyterms_prompt": '["Quorvex","Zyntrix Flow"]'}),
          "silent", SK, S, UNKNOWN_Q, fix_tokens=["keyterms_prompt"], offender_tokens=["word_boost"],
          by_construction=True),
    Probe("stream.param.camel_case", "streaming", "`speakerLabels=true` instead of `speaker_labels=true`",
          _stream({**M36, "speakerLabels": "true"}, {**M36, "speaker_labels": "true"}),
          "silent", "author", S, UNKNOWN_Q, fix_tokens=["speaker_labels"], offender_tokens=["speakerLabels"],
          effect=_begin("speaker_labels", True),
          note="camelCase is how the Node SDK spells the option; the rule quoted is the skill's general one."),
    Probe("stream.value.mode", "streaming", "`mode=max-accuracy` (hyphen) instead of `max_accuracy`",
          _stream({**M36, "mode": "max-accuracy"}, {**M36, "mode": "max_accuracy"}), "unspecified", "author",
          fix_tokens=["max_accuracy"], offender_tokens=["max-accuracy", "mode"],
          effect=_begin("mode", "max_accuracy"),
          note="A bad *value* for a real parameter, as opposed to an unknown parameter name."),
    Probe("stream.value.domain", "streaming", "`domain=medical` instead of `medical-v1`",
          _stream({**M36, "domain": "medical"}, {**M36, "domain": "medical-v1"}), "unspecified", SK, S,
          '| Medical Mode `domain: "medical"` | Correct value is `domain: "medical-v1"` |',
          fix_tokens=["medical-v1"], offender_tokens=["domain"], effect=_begin("domain", "medical-v1")),
    Probe("stream.param.v2_silence", "streaming", "v2-era `end_utterance_silence_threshold=1000`",
          _stream({**M36, "end_utterance_silence_threshold": "1000"}, M36), "silent", "author", S, UNKNOWN_Q,
          fix_tokens=["min_turn_silence", "max_turn_silence"], offender_tokens=["end_utterance_silence_threshold"],
          by_construction=True),
    Probe("stream.keyterms.csv", "streaming", "`keyterms_prompt` as a comma string instead of a JSON array",
          _stream({**M36, "keyterms_prompt": "Quorvex,Zyntrix Flow"},
                  {**M36, "keyterms_prompt": '["Quorvex","Zyntrix Flow"]'}),
          "unspecified", SK, STREAM, "must be JSON.stringify'd",
          fix_tokens=["json", "array"], offender_tokens=["keyterms_prompt"],
          effect=_no_echo("keyterms are not echoed in Begin.configuration")),
    Probe("stream.pacing.fast", "streaming", "replaying a file without pacing (12 s of audio sent at once)",
          _stream_fast, "reject", SK, STREAM, "audio was sent faster than real-time",
          fix_tokens=["real-time", "realtime", "real time", "pace", "faster"], offender_tokens=["duration"],
          effect=_accepted_is_applied("server accepted unpaced audio"), fixtures=[CALL, EN]),
    Probe("stream.url.v2", "streaming", "hardcoded v2 realtime URL", _stream_v2, "honored", SK, S,
          "| Hardcoding v2 streaming URL | v3 (`/v3/ws`) is current; v2 still works but is legacy |",
          fix_tokens=["v3", "streaming.assemblyai.com"], offender_tokens=["v2"], effect=_v2_effect),

    # ------------------------------------------------------------------ sync (POST /v1/transcribe)
    Probe("sync.config.unknown_field", "sync", "async-only `speaker_labels` in the Sync `config`",
          _sync({"speaker_labels": True}, {}), "reject", SK, S,
          "**Unknown `config` fields are rejected with a 400**", offender_tokens=["speaker_labels"], kill=True),
    Probe("sync.header.missing_model", "sync", "no `X-AAI-Model` header", _sync({}, {}, model_wrong=None),
          "unspecified", SK, S, "**Required header:** `X-AAI-Model: universal-3-5-pro`",
          fix_tokens=["x-aai-model"]),
    Probe("sync.header.model_u36", "sync", "`X-AAI-Model: universal-3-6-pro` (streaming-only model)",
          _sync({}, {}, model_wrong="universal-3-6-pro"), "unspecified", SK, S,
          "Use `universal-3-5-pro` in async `speech_models` and the Sync `X-AAI-Model` header",
          fix_tokens=["universal-3-5-pro", "u3-sync-pro"], offender_tokens=["universal-3-6-pro"],
          effect=_served_not_discriminating("Sync responses do not echo the model, so which model ran "
                                            "is not visible")),
    Probe("sync.config.word_boost", "sync", "`word_boost` alias in the Sync `config`",
          _sync({"word_boost": TERMS}, {"keyterms_prompt": TERMS}), "honored", SK, S,
          "the aliases `word_boost` and `keyterms` are still accepted",
          fix_tokens=["keyterms_prompt"], offender_tokens=["word_boost"],
          effect=_no_echo("Sync responses do not echo the config")),
    Probe("sync.audio.mp3", "sync", "MP3 audio", _sync({}, {}, audio_wrong=(MP3, "audio/mpeg")),
          "reject", SK, APIREF, "| 415 | `unsupported_media_type` | Unsupported format",
          fix_tokens=["wav", "pcm"], offender_tokens=["mpeg", "mp3"], fixtures=[EN, MP3],
          effect=_transcribed(EN_MUST_CONTAIN)),
    Probe("sync.auth.bearer", "sync", "`Authorization: Bearer KEY`", _sync({}, {}, auth_wrong="bearer"),
          "honored", SK, S, "Bearer prefix optional here, unlike the async REST API",
          fix_tokens=["bearer"], effect=_accepted_is_applied("request was authenticated")),

    # ------------------------------------------------------------------ dictation (POST /v1/transcribe/live)
    Probe("dictation.config.unknown_field", "dictation", "async-only `redact_pii` in the Dictation `config`",
          _dictation({"redact_pii": True}), "reject", SK, S, "unknown → `400`",
          offender_tokens=["redact_pii"], kill=True),
    Probe("dictation.config.model", "dictation", "`model` in the Dictation `config`",
          _dictation({"model": "universal-3-5-pro"}), "reject", SK, S, "Dictation takes no model at all",
          offender_tokens=["model"]),
    Probe("dictation.multipart.audio_first", "dictation", "`audio` part sent before `config`",
          _dictation({}, order_wrong=("audio", "config")), "reject", SK, S,
          "must be the **first** multipart part", fix_tokens=["first", "before", "order"],
          offender_tokens=["config"]),
    Probe("dictation.multipart.no_config", "dictation", "no `config` part at all",
          _dictation({}, order_wrong=("audio",)), "reject", SK, S,
          "audio-before-config or a missing config is a `400`", fix_tokens=["config"]),
    Probe("dictation.config.language_string", "dictation", "`language_codes: \"en\"` (string, not a list)",
          _dictation({"language_codes": "en"}), "unspecified", SK, S,
          "It's always a **list**: `language_codes: [\"es\"]`",
          fix_tokens=["list", "array"], offender_tokens=["language_codes"],
          effect=_served_not_discriminating("the clip is English, which the default also produces, so "
                                            "whether the string was read as a language hint is not visible")),
    Probe("dictation.auth.bearer", "dictation", "`Authorization: Bearer KEY`",
          _dictation({}, auth_wrong="bearer"), "unspecified", SK, S,
          "**Auth:** `Authorization: YOUR_API_KEY` (raw key, no Bearer)",
          fix_tokens=["bearer"], effect=_accepted_is_applied("request was authenticated")),
    Probe("dictation.audio.mp3", "dictation", "MP3 audio", _dictation({}, audio_wrong=(MP3, "audio/mpeg")),
          "reject", SK, S, "**Compressed formats (MP3/M4A/FLAC/OGG/WebM) → `415`**",
          fix_tokens=["wav", "pcm"], offender_tokens=["mpeg", "mp3"], fixtures=[EN, MP3]),

    # ------------------------------------------------------------------ text-to-speech (POST /v1/speech)
    Probe("tts.voice_param", "tts", "`voice` instead of `voice_id`",
          _tts({"text": TTS_OK["text"], "voice": "aai-jane", "stream": False}), "reject", SK, S,
          "| TTS `voice: \"aai-kyla\"` | Rejected with validation error",
          fix_tokens=["voice_id"], offender_tokens=["voice"], kill=True, fixtures=[]),
    Probe("tts.voice_bare_name", "tts", "bare voice name (`voice_id: \"jane\"`)",
          _tts({**TTS_OK, "voice_id": "jane"}), "reject", SK, S,
          "`voice_not_found` — TTS takes the library slug, `aai-jane`",
          fix_tokens=["aai-"], offender_tokens=["jane", "voice"], fixtures=[]),
    Probe("tts.unknown_field", "tts", "a parameter that does not exist (`zz_not_a_real_param`)",
          _tts({**TTS_OK, "zz_not_a_real_param": True}), "warn", SK, TTSREF,
          "Unknown fields are ignored and listed in `X-TTS-Unknown-Params`",
          offender_tokens=["zz_not_a_real_param"], by_construction=True, kill=True, fixtures=[]),
    Probe("tts.response_format", "tts", "OpenAI-style `response_format: \"wav\"` instead of `container: \"wav\"`",
          _tts({**TTS_OK, "response_format": "wav"}, {**TTS_OK, "container": "wav"}), "warn", "author", TTSREF,
          "Unknown fields are ignored and listed in `X-TTS-Unknown-Params`",
          fix_tokens=["container"], offender_tokens=["response_format"], effect=_riff_effect, fixtures=[],
          note="The caller wanted a playable WAV; the default output is headerless PCM."),
    Probe("tts.wav_with_mp3", "tts", "`container: \"wav\"` together with `encoding: \"mp3\"`",
          _tts({**TTS_OK, "container": "wav", "encoding": "mp3"}), "reject", SK, S,
          "| Setting `container=\"wav\"` with `encoding=\"mp3\"` | Rejected with validation error",
          fix_tokens=["pcm"], offender_tokens=["container", "encoding"], fixtures=[]),
    Probe("tts.wrong_host", "tts", "TTS request sent to `api.assemblyai.com`",
          _tts(TTS_OK, host_wrong=A), "reject", SK, S,
          "| Calling `https://api.assemblyai.com` for TTS | 404",
          fix_tokens=["tts."], fixtures=[]),
    Probe("tts.auth.bearer", "tts", "`Authorization: Bearer KEY`", _tts(TTS_OK, auth_wrong="bearer"),
          "unspecified", "author", fix_tokens=["bearer"],
          effect=_accepted_is_applied("request was authenticated"), fixtures=[]),
    Probe("tts.speed", "tts", "`speed: 1.25`", _tts({**TTS_OK, "speed": 1.25}), "reject", SK, TTSREF,
          "Only `1` is accepted: there is no speed control", offender_tokens=["speed"], fixtures=[]),
]

BY_ID = {p.id: p for p in PROBES}


def _keyterms_echo(rec: dict, aux: dict) -> Effect:
    t = final(rec)
    if "keyterms_prompt" not in t:
        return UNVERIFIABLE, "transcript does not echo `keyterms_prompt`"
    return (APPLIED if set(TERMS) <= set(t["keyterms_prompt"] or []) else NOT_APPLIED), \
        f"keyterms_prompt={t['keyterms_prompt']!r}"


for _pid, _fn in {
    "async.word_boost": _keyterms_echo,
    "async.speech_understanding.no_request": _translation_effect,
    "async.domain.medical": _domain_effect,
    "async.domain.unsupported_language": _domain_effect,
    "async.sentiment.non_english": _sentiment_effect,
    "async.prompt.instructions": _verbatim_effect,
    "async.pii.policy_name": _policy_effect,
    "stream.param.model_name": _begin("model", "universal-streaming-english"),
    "stream.param.camel_case": _begin("speaker_labels", True),
    "stream.value.mode": _begin("mode", "max_accuracy"),
    "stream.value.domain": _begin("domain", "medical-v1"),
    "tts.response_format": _riff_effect,
    "sync.audio.mp3": _transcribed(EN_MUST_CONTAIN),
}.items():
    BY_ID[_pid].control_effect = _fn
KILL = [p for p in PROBES if p.kill]
assert len(BY_ID) == len(PROBES), "duplicate probe id"
assert len(KILL) == 10, f"kill test must be exactly 10 probes, got {len(KILL)}"
