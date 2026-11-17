import json

from twohundred import fixtures as fx
from twohundred.audio import duration_s, read_wav
from twohundred.config import AUDIO
from twohundred.probes import PROBES, filler_count


def test_digit_matching():
    c = fx.contains_digits
    assert c("It's nine eight seven, six five, four three two one.", "987654321")
    assert c("My SSN is 987-65-4321.", "987654321")
    assert not c("My SSN is ###-##-####, phone 415-555-0142.", "987654321")
    assert not c("987 then some words 654321", "987654321")
    assert c("Call 415 555 0142", "4155550142")


def test_filler_count():
    assert filler_count("Um, so I think we should, uh, organise it. Uhm.") == 3
    assert filler_count("The summer umbrella hummed.") == 0


def test_committed_audio_matches_manifest_and_format():
    assert fx.verify_manifest() == []
    manifest = json.loads(fx.MANIFEST.read_text())
    for name in manifest["files"]:
        if name.endswith(".wav"):
            pcm, sr, ch = read_wav(AUDIO / name)
            assert (sr, ch) == (16000, 1)
            assert 3 < duration_s(pcm) < 120, name   # inside the Sync/Dictation 120 s limit


def test_every_fixture_a_probe_needs_exists():
    needed = {f for p in PROBES for f in p.fixtures}
    assert all((AUDIO / f).is_file() for f in needed), needed


def test_ground_truth_is_in_the_scripts():
    call = next(s for s in fx.scripts() if s["id"] == "support_call")
    spoken = " ".join(seg["text"] for seg in call["segments"])
    assert fx.contains_digits(spoken, call["ground_truth"]["ssn_digits"])
    assert fx.contains_digits(spoken, call["ground_truth"]["phone_digits"])
    assert {seg["speaker"] for seg in call["segments"]} == {"A", "B"}
