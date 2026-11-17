"""F6: no account identifiers in saved records, and the trimmer drops them."""
import re
from pathlib import Path

from twohundred.transport import _trim_transcript

RESULTS = Path(__file__).resolve().parent.parent / "results"
KEYED = re.compile(r"""(project_id|token_id)\?["']?\s*[:=]\s*\d{4,}""")
UPLOAD = re.compile(r"cdn\.assemblyai\.com/upload/[A-Za-z0-9-]{8,}")


def test_trim_drops_account_ids():
    out = _trim_transcript({"id": "x", "project_id": 1234567, "token_id": 7654321,
                            "audio_url": "https://cdn.assemblyai.com/upload/0a1b2c3d-1111-2222-3333-444455556666"})
    assert "project_id" not in out and "token_id" not in out
    assert out["audio_url"] == "https://cdn.assemblyai.com/upload/[REDACTED:upload_id]"


def test_no_account_ids_in_results():
    bad = []
    for p in RESULTS.rglob("*"):
        if p.is_file() and p.suffix in {".json", ".jsonl", ".md", ".csv", ".txt", ".log"}:
            s = p.read_text(encoding="utf-8", errors="replace")
            if KEYED.search(s) or UPLOAD.search(s):
                bad.append(p.relative_to(RESULTS).as_posix())
    assert not bad, f"account identifiers in {len(bad)} files, e.g. {bad[:3]}; run scripts/redact_account_ids.py"
