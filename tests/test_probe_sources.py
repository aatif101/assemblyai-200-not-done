"""Every sentence attributed to AssemblyAI's skill must be an exact quote from it.

Needs a checkout of AssemblyAI/assemblyai-skill at the pinned commit:
    SKILL_REPO=/path/to/assemblyai-skill python -m pytest tests/test_probe_sources.py
Skipped when the checkout is not available.
"""
import os
import subprocess
from pathlib import Path

import pytest

from twohundred.config import SKILL_COMMIT
from twohundred.probes import KILL, PROBES

CANDIDATES = [os.environ.get("SKILL_REPO"), "../assemblyai-skill", "../assemblyai/assemblyai-skill"]
REPO = next((Path(c).resolve() for c in CANDIDATES if c and (Path(c) / "skills/assemblyai/SKILL.md").is_file()), None)


def test_probe_list_shape():
    assert len({p.id for p in PROBES}) == len(PROBES) >= 40
    assert len(KILL) == 10
    for p in PROBES:
        assert p.documented in {"reject", "silent", "warn", "honored", "late-fail", "unspecified"}
        assert p.origin in {"skill", "author"}
        if p.origin == "skill":
            assert p.source_file and p.source_quote, f"{p.id}: skill probes must quote the skill"
        assert p.fix_tokens or p.offender_tokens, f"{p.id}: no way to judge an error message"


@pytest.mark.skipif(REPO is None, reason="assemblyai-skill checkout not found (set SKILL_REPO)")
def test_quotes_are_verbatim_at_pinned_commit():
    head = subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    assert head == SKILL_COMMIT, f"skill checkout is at {head[:7]}, probes are pinned to {SKILL_COMMIT[:7]}"
    missing = []
    for p in PROBES:
        if not p.source_quote:
            continue
        text = (REPO / "skills/assemblyai" / p.source_file).read_text(encoding="utf-8")
        if p.source_quote not in text:
            missing.append(f"{p.id}: not found in {p.source_file}: {p.source_quote!r}")
    assert not missing, "\n".join(missing)
