"""Decision-table tests for the classifier.

The records below are hand-written inputs that exercise each branch of the decision
table. They are not API responses, are never written to results/, and say nothing
about how AssemblyAI behaves.
"""
from twohundred.classify import classify, classify_one, matches_doc, outcome, signals
from twohundred.probes import BY_ID

OK_HTTP = {"request": {"kind": "http"}, "response": {"status": 200, "headers": {}, "json": {"id": "t"}},
           "final": {"status": "completed", "text": "hello"}}
OK_WS = {"request": {"kind": "ws"}, "ws": {"handshake_status": 101, "messages": [],
         "begin": {"type": "Begin", "configuration": {"model": "universal-3-6-pro", "speaker_labels": True}}}}


def http(status, body=None, final=None, headers=None):
    rec = {"request": {"kind": "http"}, "response": {"status": status, "headers": headers or {}, "json": body or {}}}
    if final is not None:
        rec["final"] = final
    return rec


def ws(cfg=None, close=None, reason="", handshake=101, body="", messages=None):
    w = {"handshake_status": handshake, "messages": messages or []}
    if cfg is not None:
        w["begin"] = {"type": "Begin", "configuration": cfg}
    if close is not None:
        w.update(closed_by_server=True, server_close_code=close, server_close_reason=reason)
    if body:
        w["handshake_body"] = body
    return {"request": {"kind": "ws"}, "ws": w}


def test_rejection_naming_the_fix_is_instructive():
    p = BY_ID["async.word_boost"]
    v = classify_one(p, http(400, {"error": "word_boost is not supported, use keyterms_prompt"}), OK_HTTP, {})
    assert (v.cls, v.names_fix, v.names_offender) == ("instructive_error", True, True)


def test_rejection_not_naming_the_fix_is_opaque():
    v = classify_one(BY_ID["async.word_boost"], http(400, {"error": "Invalid request body"}), OK_HTTP, {})
    assert (v.cls, v.names_fix, v.names_offender) == ("opaque_error", False, False)


def test_unknown_field_probe_is_instructive_when_error_names_the_field():
    p = BY_ID["sync.config.unknown_field"]
    assert classify_one(p, http(400, {"detail": "unknown field: speaker_labels"}), OK_HTTP, {}).cls == "instructive_error"
    assert classify_one(p, http(400, {"detail": "bad request"}), OK_HTTP, {}).cls == "opaque_error"


def test_accepted_unknown_field_without_warning_is_silent():
    v = classify_one(BY_ID["async.unknown_field"], OK_HTTP, OK_HTTP, {})
    assert v.cls == "silent_accept"


def test_accepted_with_a_warning_header_is_not_silent():
    p = BY_ID["tts.unknown_field"]
    control = http(200, headers={"X-TTS-Region": "us-east-1"})
    mistake = http(200, headers={"X-TTS-Region": "us-east-1", "X-TTS-Unknown-Params": "zz_not_a_real_param"})
    v = classify_one(p, mistake, control, {})
    assert v.cls == "accepted_with_warning" and "X-TTS-Unknown-Params" in v.signals[0]


def test_a_warning_also_present_on_the_control_does_not_count():
    hdr = {"Warning": "299 - always here"}
    assert classify_one(BY_ID["async.unknown_field"], http(200, headers=hdr, final={"status": "completed"}),
                        http(200, headers=hdr, final={"status": "completed"}), {}).cls == "silent_accept"


def test_body_warning_counts():
    p = BY_ID["async.domain.unsupported_language"]
    control = http(200, final={"status": "completed", "metadata": {"domain_used": "medical-v1"}})
    mistake = http(200, final={"status": "completed",
                               "metadata": {"domain_used": None, "warnings": [{"message": "skipped"}]}})
    assert classify_one(p, mistake, control, {}).cls == "accepted_with_warning"


def test_stream_echo_decides_applied_vs_silent():
    p = BY_ID["stream.param.model_name"]
    control = ws({"model": "universal-streaming-english"})
    assert classify_one(p, ws({"model": "universal-3-6-pro"}), control, {}).cls == "silent_accept"
    assert classify_one(p, ws({"model": "universal-streaming-english"}), control, {}).cls == "honored"


def test_effect_is_unverifiable_when_the_control_does_not_show_it():
    p = BY_ID["stream.param.camel_case"]
    v = classify_one(p, ws({"speaker_labels": False}), ws({"speaker_labels": False}), {})
    assert v.cls == "accepted_unverified"


def test_failed_control_invalidates_the_row():
    v = classify_one(BY_ID["async.word_boost"], http(400, {"error": "x"}), http(401, {"error": "bad key"}), {})
    assert v.cls == "invalid_control"


def test_late_failure_is_a_rejection_at_processing_stage():
    rec = http(200, {"id": "t"}, final={"status": "error", "error": "Transcoding failed. File type application/json"})
    o = outcome(rec)
    assert (o.state, o.stage) == ("rejected", "processing")
    assert matches_doc("late-fail", "opaque_error", o.stage) == "yes"


def test_server_errors_and_timeouts_are_inconclusive():
    assert outcome(http(503)).state == "inconclusive"
    assert outcome({"request": {"kind": "http"}, "transport_error": "ReadTimeout"}).state == "inconclusive"
    assert outcome(http(200, final={"poll_timeout": True})).state == "inconclusive"
    assert outcome(ws(close=1011)).state == "inconclusive"


def test_websocket_rejections():
    assert outcome(ws(handshake=401, body="Unauthorized")).state == "rejected"
    assert outcome(ws(close=1008, reason="Unauthorized")).stage == "handshake"
    o = outcome(ws({"model": "x"}, close=3007, reason="Input duration violation"))
    assert (o.state, o.stage, o.code) == ("rejected", "session", "close 3007")
    assert outcome(ws({"model": "x"}, close=1000)).state == "accepted"


def test_repeats_that_disagree_are_unstable():
    p = BY_ID["async.unknown_field"]
    row = classify(p, [OK_HTTP, http(400, {"error": "zz_not_a_real_param"})], [OK_HTTP, OK_HTTP], {})
    assert row["class"] == "unstable"


def test_matches_doc():
    assert matches_doc("reject", "opaque_error", "request") == "yes"
    assert matches_doc("reject", "silent_accept", "request") == "NO"
    assert matches_doc("silent", "silent_accept", "") == "yes"
    assert matches_doc("warn", "silent_accept", "") == "NO"
    assert matches_doc("honored", "accepted_unverified", "") == "consistent (not verifiable)"
    assert matches_doc("unspecified", "silent_accept", "") == "n/a"


def test_signals_ignores_empty_values():
    assert signals(http(200, headers={"X-TTS-Unknown-Params": ""})) == set()


def test_generic_words_do_not_count_as_naming_the_fix():
    p = BY_ID["async.speech_understanding.no_request"]
    assert classify_one(p, http(400, {"error": "Bad request: invalid body"}), OK_HTTP, {}).cls == "opaque_error"
    v = classify_one(p, http(400, {"error": 'features must be nested under "request"'}), OK_HTTP, {})
    assert v.cls == "instructive_error"


# ---------------------------------------------------------------- F1 and F2 (added 2026-11-15, after Phase 1 data)


def test_a_warning_naming_the_input_overrides_an_echo():
    p = BY_ID["async.word_boost"]
    terms = ["Quorvex", "Zyntrix Flow"]
    control = http(200, final={"status": "completed", "keyterms_prompt": terms})
    mistake = http(200, final={"status": "completed", "word_boost": terms, "keyterms_prompt": [],
                               "metadata": {"warnings": [{"message": "Skipped word_boost because it is not supported."}]}})
    v = classify_one(p, mistake, control, {})
    assert (v.cls, v.effect) == ("accepted_with_warning", "not_applied")


def test_a_warning_that_does_not_name_the_input_leaves_an_echo_applied():
    p = BY_ID["async.word_boost"]
    terms = ["Quorvex", "Zyntrix Flow"]
    control = http(200, final={"status": "completed", "keyterms_prompt": terms})
    mistake = http(200, final={"status": "completed", "keyterms_prompt": terms,
                               "metadata": {"warnings": [{"message": "Audio was very short."}]}})
    assert classify_one(p, mistake, control, {}).cls == "honored"


SYNC_OK = http(200, {"text": "The quarterly review is scheduled for Tuesday at 10:30."})


def test_sync_mp3_is_honored_when_the_transcript_is_correct():
    p = BY_ID["sync.audio.mp3"]
    assert classify_one(p, SYNC_OK, SYNC_OK, {}).cls == "honored"
    garbled = http(200, {"text": "static"})
    assert classify_one(p, garbled, SYNC_OK, {}).cls == "silent_accept"


def test_settings_a_correct_transcript_cannot_discriminate_stay_unverifiable():
    for pid in ("sync.header.model_u36", "dictation.config.language_string"):
        v = classify_one(BY_ID[pid], SYNC_OK, SYNC_OK, {})
        assert v.cls == "accepted_unverified" and "correct transcript returned" in v.evidence


def test_a_deprecation_notice_naming_the_input_does_not_override_an_applied_effect():
    p = BY_ID["async.summarization.top_level"]
    ok = {"status": "completed", "summary": "a summary"}
    mistake = http(200, final={**ok, "metadata": {"warnings": [
        {"message": 'Starting September 15, 2026, requests with a "summarization" parameter will return an error.'}]}})
    assert classify_one(p, mistake, http(200, final=ok), {}).cls == "honored"
