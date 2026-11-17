"""D2 rule: WRITEUP.md stays at or under 600 words (tables and headings counted; image and link targets not)."""
import re
from pathlib import Path

WRITEUP = Path(__file__).resolve().parent.parent / "WRITEUP.md"


def test_writeup_word_limit():
    s = WRITEUP.read_text(encoding="utf-8")
    s = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", s)
    s = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", s)
    s = re.sub(r"^\|[-| ]+\|$", "", s, flags=re.M)
    assert len(re.findall(r"[A-Za-z0-9][A-Za-z0-9'’._%/+-]*", s)) <= 600
