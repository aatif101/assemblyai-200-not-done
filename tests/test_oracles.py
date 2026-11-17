"""Oracle tests on hand-written run directories. Not API responses; never written to results/."""
import json

from twohundred.oracles import code_scan, grade, schema_errors

SSN_SAID = "It's 987-65-4321."
PHONE_SAID = "Call 415-555-0142."


def rundir(tmp_path, task, out, server=None, frames=None, exit_code=0, **contract):
    d = tmp_path / task
    (d / "server").mkdir(parents=True)
    (d / "contract.json").write_text(json.dumps({"exit_code": exit_code, **contract}))
    if out is not None:
        (d / "out.json").write_text(json.dumps(out))
    if server is not None:
        (d / "server" / "transcript.json").write_text(json.dumps(server))
    if frames is not None:
        (d / "out.json.frames.jsonl").write_text("\n".join(json.dumps(f) for f in frames))
    return d


def server_t(**kw):
    return {"id": "abc", "status": "completed", **kw}


# ---------------------------------------------------------------- T1


GOOD_CFG = {"model": "universal-3-6-pro", "domain": "medical-v1", "speaker_labels": True, "redact_pii": True}


def t1_frames(cfg, final_text="My number is ###-##-####."):
    return [{"type": "Begin", "configuration": cfg},
            {"type": "Turn", "end_of_turn": False, "transcript": "My number"},
            {"type": "Turn", "end_of_turn": True, "transcript": final_text},
            {"type": "Termination"}]


def test_t1_passes_on_echo_and_redacted_finals(tmp_path):
    d = rundir(tmp_path, "t1_stream_clinic", {"turns": [{"speaker": "A", "text": "###"}]}, frames=t1_frames(GOOD_CFG))
    g = grade("t1_stream_clinic", d)
    assert g["runs"] and g["outcome"] and not g["silent_failure"]


def test_t1_ignored_camelcase_is_a_silent_failure(tmp_path):
    cfg = {**GOOD_CFG, "speaker_labels": False}
    d = rundir(tmp_path, "t1_stream_clinic", {"turns": [{"speaker": None, "text": "###"}]}, frames=t1_frames(cfg))
    g = grade("t1_stream_clinic", d)
    assert g["runs"] and not g["outcome"] and g["silent_failure"]
    assert not g["checks"]["speaker_labels"]["pass"]


def test_t1_ssn_in_final_turn_fails(tmp_path):
    d = rundir(tmp_path, "t1_stream_clinic", {"turns": [{"speaker": "A", "text": "x"}]},
               frames=t1_frames(GOOD_CFG, SSN_SAID))
    assert not grade("t1_stream_clinic", d)["checks"]["ssn_absent_server_turns"]["pass"]


def test_t1_no_frames_fails(tmp_path):
    d = rundir(tmp_path, "t1_stream_clinic", {"turns": [{"speaker": "A", "text": "x"}]})
    assert not grade("t1_stream_clinic", d)["outcome"]


# ---------------------------------------------------------------- T2


def t2_server(**kw):
    base = dict(audio_duration=51, speaker_labels=True, redact_pii=True,
                redact_pii_policies=["person_name", "phone_number", "us_social_security_number"],
                text="This is ####. My SSN is ###-##-####.",
                utterances=[{"speaker": "A", "text": "This is ####."}, {"speaker": "B", "text": "###-##-####"}])
    return server_t(**{**base, **kw})


T2_OUT = {"transcript_id": "abc", "text": "This is ####.", "utterances": [{"speaker": "A", "text": "####"}]}


def test_t2_passes(tmp_path):
    assert grade("t2_async_redact", rundir(tmp_path, "t2_async_redact", T2_OUT, t2_server()))["outcome"]


def test_t2_missing_policy_or_leaked_digits_fails(tmp_path):
    g = grade("t2_async_redact", rundir(tmp_path, "t2_async_redact", T2_OUT,
                                       t2_server(redact_pii_policies=["us_social_security_number"])))
    assert not g["checks"]["policies_cover_request"]["pass"] and g["silent_failure"]
    leak = t2_server(text=SSN_SAID + " " + PHONE_SAID, redact_pii=False)
    g = grade("t2_async_redact", rundir(tmp_path / "b", "t2_async_redact", T2_OUT, leak))
    assert not g["checks"]["ssn_absent_server"]["pass"] and not g["checks"]["phone_absent_server"]["pass"]


def test_t2_wrong_audio_or_id_fails(tmp_path):
    g = grade("t2_async_redact", rundir(tmp_path, "t2_async_redact", {**T2_OUT, "transcript_id": "other"},
                                       t2_server(audio_duration=8)))
    assert not g["checks"]["id_matches_output"]["pass"] and not g["checks"]["is_the_fixture"]["pass"]


# ---------------------------------------------------------------- T3


UK = "So I think we should organise the colour samples. The centre team has catalogued them."
US = "So I think we should organize the color samples. The center team has cataloged them."


def test_t3_server_british_passes(tmp_path):
    d = rundir(tmp_path, "t3_british", {"transcript_id": "abc", "text": UK},
               server_t(audio_duration=12, text=UK, language_code="en_uk"))
    assert grade("t3_british", d)["outcome"]


def test_t3_local_rewrite_does_not_pass(tmp_path):
    d = rundir(tmp_path, "t3_british", {"transcript_id": "abc", "text": UK},
               server_t(audio_duration=12, text=US, language_code="en"))
    g = grade("t3_british", d)
    assert g["checks"]["british_output"]["pass"] and not g["checks"]["british_server"]["pass"]
    assert g["silent_failure"]


# ---------------------------------------------------------------- T4


T4_TEXT = "The Quorvex dashboard ships. Zyntrix Flow is blocked. Talvanta moves."


def t4_out(**kw):
    return {"transcript_id": "abc", "text": T4_TEXT, "utterances": [{"speaker": "A", "text": T4_TEXT}],
            "summary": "Two products ship.", **kw}


def test_t4_passes(tmp_path):
    s = server_t(audio_duration=17, text=T4_TEXT, speaker_labels=True,
                 keyterms_prompt=["Quorvex", "Zyntrix Flow", "Talvanta"])
    assert grade("t4_legacy_upgrade", rundir(tmp_path, "t4_legacy_upgrade", t4_out(), s))["outcome"]


def test_t4_word_boost_kept_is_a_silent_failure(tmp_path):
    bad = "The Corvex dashboard ships. Zintrix flow is blocked. Talvanta moves."
    s = server_t(audio_duration=17, text=bad, speaker_labels=True, word_boost=["Quorvex"], keyterms_prompt=[],
                 metadata={"warnings": [{"message": "Skipped word_boost"}]})
    g = grade("t4_legacy_upgrade", rundir(tmp_path, "t4_legacy_upgrade", t4_out(text=bad, utterances=[]), s))
    assert g["runs"] and g["silent_failure"]


def test_t4_crash_is_not_runs(tmp_path):
    g = grade("t4_legacy_upgrade", rundir(tmp_path, "t4_legacy_upgrade", None, None, exit_code=1))
    assert not g["runs"] and not g["outcome"] and not g["silent_failure"]


def test_cap_hit_fails_both(tmp_path):
    s = server_t(audio_duration=17, text=T4_TEXT, speaker_labels=True)
    g = grade("t4_legacy_upgrade", rundir(tmp_path, "t4_legacy_upgrade", t4_out(), s, cap_hit="turns"))
    assert not g["runs"] and not g["outcome"]


# ---------------------------------------------------------------- helpers


def test_schema_errors():
    schema = {"type": "object", "required": ["a"], "properties": {"a": {"type": "string", "minLength": 1}}}
    assert schema_errors({"a": "x"}, schema) == []
    assert schema_errors({"a": ""}, schema) and schema_errors({}, schema) and schema_errors([], schema)


def test_code_scan_skips_dependencies(tmp_path):
    (tmp_path / "app.py").write_text("q = {'word_boost': []}\n")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "x.js").write_text("end_utterance_silence_threshold\n")
    assert code_scan(tmp_path) == ["app.py:1: word_boost"]


def test_t2_deleted_transcript_fails_with_a_clear_reason(tmp_path):
    g = grade("t2_async_redact", rundir(tmp_path, "t2_async_redact", T2_OUT, t2_server(text="Deleted by user.",
                                                                                       utterances=None)))
    assert not g["outcome"] and not g["checks"]["transcript_not_deleted"]["pass"]
