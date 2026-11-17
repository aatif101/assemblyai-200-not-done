"""Decision rules for the Phase 2 pre-checks. Hand-written inputs, not API responses."""
from twohundred.precheck import LONG, PRODUCT, VERBATIM, count_forms, decide_q3, decide_q4

UK = "Um, so I think we should organise the colour samples. The centre team has catalogued them."
US = "Um, so I think we should organize the color samples. The center team has cataloged them."


def t(text, code="en", status="completed"):
    return {"response": {"status": 200, "json": {"id": "x"}},
            "final": {"status": status, "text": text, "language_code": code, "speech_model_used": "m"}}


def clip(base, loc, code=None):
    return {"baseline": base, "localized": loc, "code_en_uk": code or t(US)}


def test_count_forms_is_whole_word():
    assert count_forms(UK, ["colour", "organise"]) == ["colour", "organise"]
    assert count_forms("colours", ["colour"]) == []


def test_short_clip_passing_picks_the_short_clip():
    r = decide_q3({VERBATIM: clip(t(US), t(UK, "en_uk")), LONG: clip(t(US), t(US))})
    assert (r["verdict"], r["t3"], r["t3_fixture"]) == ("gradable", "british_spelling", VERBATIM)


def test_only_long_clip_passing_picks_the_long_clip():
    r = decide_q3({VERBATIM: clip(t(US), t(US, "en_us")), LONG: clip(t(US), t(UK, "en_uk"))})
    assert (r["verdict"], r["t3_fixture"]) == ("gradable_long_clip", f"{LONG}.wav")


def test_british_text_without_en_uk_code_does_not_pass():
    r = decide_q3({VERBATIM: clip(t(US), t(UK, "en")), LONG: clip(t(US), t(UK, "en"))})
    assert (r["verdict"], r["t3"], r["t3_fixture"]) == ("not_gradable", "key_terms", PRODUCT)


def test_baseline_already_british_means_not_gradable():
    r = decide_q3({VERBATIM: clip(t(UK), t(UK, "en_uk")), LONG: clip(t(UK), t(UK, "en_uk"))})
    assert r["verdict"] == "not_gradable"


def test_rejected_localized_request_does_not_pass():
    bad = {"response": {"status": 400, "json": {"error": "x"}}}
    r = decide_q3({VERBATIM: clip(t(US), bad), LONG: clip(t(US), bad)})
    assert r["verdict"] == "not_gradable"


def test_missing_records_are_not_gradable():
    assert decide_q3({})["verdict"] == "not_gradable"


def test_q4_works_only_with_a_successful_nonempty_summary():
    ok = {"response": {"status": 200}, "final": {"status": "completed", "speech_understanding": {
        "response": {"summarization": {"status": "success", "block_summary": "Two products ship."}}}}}
    assert decide_q4(ok)["t4_includes_lemur"] is True
    empty = {"response": {"status": 200}, "final": {"status": "completed", "speech_understanding": {
        "response": {"summarization": {"status": "success", "block_summary": "  "}}}}}
    assert decide_q4(empty)["t4_includes_lemur"] is False
    rejected = {"response": {"status": 400, "json": {"error": "not available"}}}
    assert decide_q4(rejected)["verdict"] == "does_not_work"
    assert decide_q4(None)["verdict"] == "does_not_work"
