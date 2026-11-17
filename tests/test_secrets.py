import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import FAKE_KEY, ROOT
from twohundred.config import load_settings
from twohundred.secrets import Scrubber


def test_scrubber_removes_every_encoding():
    import base64, urllib.parse
    s = Scrubber({"ASSEMBLYAI_API_KEY": "k/ey+with=odd&chars-0123456789"})
    key = "k/ey+with=odd&chars-0123456789"
    blob = " | ".join([key, urllib.parse.quote(key, safe=""), base64.b64encode(key.encode()).decode(),
                       base64.b64encode(("Bearer " + key).encode()).decode()])
    out = s.scrub(blob)
    assert key not in out and out.count("[REDACTED:ASSEMBLYAI_API_KEY]") == 4


def test_scrub_obj_reaches_nested_keys_and_values():
    s = Scrubber({"ASSEMBLYAI_API_KEY": FAKE_KEY})
    out = s.scrub_obj({"a": [{"url": f"wss://x/?token={FAKE_KEY}"}], FAKE_KEY: 1})
    assert FAKE_KEY not in json.dumps(out)


def test_settings_never_expose_the_key():
    settings = load_settings()
    assert FAKE_KEY not in repr(settings) and FAKE_KEY not in str(settings)


def _copy_repo(tmp: Path) -> Path:
    dst = tmp / "repo"
    shutil.copytree(ROOT / "twohundred", dst / "twohundred", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "scripts", dst / "scripts")
    (dst / "results").mkdir()
    return dst


def _run(repo: Path, env_key: str | None):
    env = {k: v for k, v in os.environ.items() if not k.endswith(("_API_KEY", "_TOKEN", "_SECRET"))}
    if env_key:
        env["ASSEMBLYAI_API_KEY"] = env_key
    return subprocess.run([sys.executable, "scripts/check_secrets.py"], cwd=repo, env=env,
                          capture_output=True, text=True)


def test_check_passes_on_a_clean_tree(tmp_path):
    r = _run(_copy_repo(tmp_path), FAKE_KEY)
    assert r.returncode == 0, r.stderr


@pytest.mark.parametrize("planted", [
    "{key}",                                  # raw
    "Authorization: Bearer {key}",            # in a header dump
    "wss://streaming.example/v3/ws?token={key}",
])
def test_check_fails_when_the_key_is_planted(tmp_path, planted):
    key = "9f8e7d6c5b4a3928" + "1706f5e4d3c2b1a0"
    repo = _copy_repo(tmp_path)
    (repo / "results" / "transcript.jsonl").write_text('{"line": "' + planted.format(key=key) + '"}\n')
    r = _run(repo, key)
    assert r.returncode == 1
    assert "results/transcript.jsonl:1" in r.stderr
    assert key not in r.stderr and key not in r.stdout, "the scanner must not print the secret"


def test_check_catches_key_shapes_even_without_knowing_the_value(tmp_path):
    repo = _copy_repo(tmp_path)
    (repo / "results" / "log.txt").write_text("key=" + "abcdef0123456789" * 2 + "\n" + "sk-ant-" + "api03-" + "A" * 24 + "\n")
    r = _run(repo, None)
    assert r.returncode == 1 and "hex32" in r.stderr and "anthropic_key" in r.stderr


def test_sha256_digests_are_not_flagged(tmp_path):
    repo = _copy_repo(tmp_path)
    (repo / "results" / "manifest.json").write_text('{"sha256": "' + "ab" * 32 + '"}\n')
    assert _run(repo, None).returncode == 0
