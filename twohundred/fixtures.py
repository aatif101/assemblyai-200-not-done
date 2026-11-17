"""Audio fixtures with ground truth known by construction.

Every clip is synthesised from a script in fixtures/scripts/*.json, so what was said
(the SSN, the product names, the number of speakers) is known exactly. Two backends:

  espeak   offline and deterministic (espeak-ng + ffmpeg). Used for the committed clips.
  aai-tts  AssemblyAI's own text-to-speech; more natural voices. Needs an API key.

`validate` then checks against the live API that each clip actually carries its ground
truth before anything is measured with it.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .audio import SAMPLE_RATE, duration_s, read_wav, sha256, silence, write_wav
from .config import AUDIO, ENDPOINTS, FIXTURES, SCRIPTS, Settings

MANIFEST = FIXTURES / "manifest.json"

VOICES = {
    "espeak": {"en": {"A": "en-us+f3", "B": "en-us+m3"}, "it": {"A": "it+f3", "B": "it+m3"}},
    "aai-tts": {"en": {"A": "aai-mary", "B": "aai-michael"}, "it": {"A": "aai-mary", "B": "aai-michael"}},
}
TTS_LANGUAGE = {"en": "english", "it": "italian"}

DIGIT_WORDS = {"zero": "0", "oh": "0", "one": "1", "two": "2", "three": "3", "four": "4",
               "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9"}


def digits_only(text: str) -> str:
    """Digits in reading order, with spelled-out digits converted ("nine eight" -> "98")."""
    out = []
    for tok in re.findall(r"[A-Za-z]+|\d", text or ""):
        if tok.isdigit():
            out.append(tok)
        elif tok.lower() in DIGIT_WORDS:
            out.append(DIGIT_WORDS[tok.lower()])
        else:
            out.append(" ")  # any other word breaks a digit run
    return "".join(out)


def contains_digits(text: str, digits: str) -> bool:
    """True if `digits` appears as one uninterrupted run (formatting such as 987-65-4321 ignored)."""
    return digits in digits_only(text)


def scripts() -> list[dict[str, Any]]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(SCRIPTS.glob("*.json"))]


def _espeak(text: str, voice: str) -> bytes:
    for tool in ("espeak-ng", "ffmpeg"):
        if not shutil.which(tool):
            raise SystemExit(f"{tool} is required for the espeak backend (it is not needed to "
                             "run the probes: the built clips are committed).")
    with tempfile.TemporaryDirectory() as tmp:
        raw, out = Path(tmp) / "raw.wav", Path(tmp) / "out.wav"
        subprocess.run(["espeak-ng", "-v", voice, "-s", "155", "-w", str(raw), text], check=True)
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(raw), "-ar", str(SAMPLE_RATE),
                        "-ac", "1", "-sample_fmt", "s16", "-bitexact", "-map_metadata", "-1", str(out)],
                       check=True)
        pcm, sr, ch = read_wav(out)
    assert sr == SAMPLE_RATE and ch == 1
    return pcm


def _aai_tts(text: str, voice: str, language: str, settings: Settings) -> bytes:
    import requests

    r = requests.post(f"{ENDPOINTS['tts']}/v1/speech", headers={"Authorization": settings.api_key},
                      json={"text": text, "voice_id": voice, "language": language, "stream": False,
                            "container": "wav", "encoding": "pcm_s16le", "sample_rate": SAMPLE_RATE},
                      timeout=120)
    if r.status_code != 200:
        raise SystemExit(f"TTS request failed with HTTP {r.status_code}: "
                         f"{settings.scrubber.scrub(r.text[:300])}")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "seg.wav"
        path.write_bytes(r.content)
        pcm, sr, ch = read_wav(path)
    if sr != SAMPLE_RATE or ch != 1:
        raise SystemExit(f"TTS returned {sr} Hz / {ch} ch audio; expected {SAMPLE_RATE} Hz mono")
    return pcm


def build(backend: str, settings: Settings | None) -> dict[str, Any]:
    """Synthesise every script and write fixtures/audio plus the manifest."""
    AUDIO.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {"backend": backend, "sample_rate": SAMPLE_RATE, "files": {}}
    for script in scripts():
        lang = script["language"]
        voices = VOICES[backend][lang]
        pcm = silence(0.3)
        for seg in script["segments"]:
            voice = voices[seg["speaker"]]
            if backend == "espeak":
                pcm += _espeak(seg["text"], voice)
            else:
                assert settings is not None
                pcm += _aai_tts(seg["text"], voice, TTS_LANGUAGE[lang], settings)
            pcm += silence(script.get("gap_seconds", 0.4))
        name = f"{script['id']}.wav"
        write_wav(AUDIO / name, pcm)
        manifest["files"][name] = {
            "script": f"fixtures/scripts/{script['id']}.json",
            "seconds": round(duration_s(pcm), 2),
            "voices": {s: voices[s] for s in sorted({seg["speaker"] for seg in script["segments"]})},
            "sha256": sha256(AUDIO / name),
        }
    mp3 = AUDIO / "short_en.mp3"
    if shutil.which("ffmpeg"):
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(AUDIO / "short_en.wav"),
                        "-codec:a", "libmp3lame", "-b:a", "64k", "-map_metadata", "-1", str(mp3)], check=True)
        manifest["files"][mp3.name] = {"derived_from": "short_en.wav", "sha256": sha256(mp3)}
    MANIFEST.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return manifest


def verify_manifest() -> list[str]:
    """Offline integrity check: every committed clip matches its recorded hash."""
    if not MANIFEST.is_file():
        return ["fixtures/manifest.json is missing (run: make fixtures)"]
    problems = []
    for name, meta in json.loads(MANIFEST.read_text(encoding="utf-8"))["files"].items():
        path = AUDIO / name
        if not path.is_file():
            problems.append(f"{name}: file missing")
        elif sha256(path) != meta["sha256"]:
            problems.append(f"{name}: sha256 differs from manifest")
    return problems
