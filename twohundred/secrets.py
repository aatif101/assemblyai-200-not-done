"""Secret handling. Every byte written to disk by this project goes through a Scrubber.

Rules enforced here and by scripts/check_secrets.py:
  * The API key is read from the environment (or a git-ignored .env) and nowhere else.
  * The key is never logged, never placed in a recorded request, never written to disk.
  * Anything serialised to results/ is scrubbed of every known secret value first.
"""
from __future__ import annotations

import base64
import os
import re
import urllib.parse
from pathlib import Path
from typing import Any, Mapping

# Names that are always treated as secrets, plus any env var with one of these suffixes.
SECRET_ENV_NAMES = (
    "ASSEMBLYAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "CLAUDE_CODE_OAUTH_TOKEN",
)
SECRET_ENV_SUFFIXES = ("_API_KEY", "_TOKEN", "_SECRET", "_PASSWORD", "_ACCESS_KEY")
MIN_SECRET_LEN = 12  # shorter values are too likely to collide with ordinary text

# Shapes that look like credentials even when we do not know the value.
SECRET_PATTERNS: dict[str, re.Pattern[str]] = {
    # AssemblyAI API keys are assumed to be 32 lowercase hex chars. The exact-value
    # check is the authoritative one; this pattern is a second net.
    "hex32": re.compile(r"(?<![0-9A-Fa-f])[0-9a-f]{32}(?![0-9A-Fa-f])"),
    "anthropic_key": re.compile(r"sk-ant-[A-Za-z0-9_\-]{16,}"),
    "generic_sk": re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9]{32,}"),
    "bearer_token": re.compile(r"Bearer\s+[A-Za-z0-9._\-]{20,}"),
    "jwt": re.compile(r"eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"),
}


def load_dotenv(path: Path) -> dict[str, str]:
    """Minimal .env reader (KEY=VALUE lines). Does not print or export anything."""
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        k, v = line.split("=", 1)
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
            v = v[1:-1]
        out[k.strip()] = v
    return out


def is_secret_name(name: str) -> bool:
    return name in SECRET_ENV_NAMES or name.endswith(SECRET_ENV_SUFFIXES)


def secret_values(env: Mapping[str, str] | None = None, dotenv: Path | None = None) -> dict[str, str]:
    """Map of secret-name -> value for every secret we know about."""
    merged: dict[str, str] = {}
    if dotenv is not None:
        merged.update(load_dotenv(dotenv))
    merged.update(dict(os.environ if env is None else env))
    return {k: v for k, v in merged.items() if is_secret_name(k) and len(v) >= MIN_SECRET_LEN}


def _encodings(value: str) -> list[str]:
    """The value plus the encodings it could plausibly appear in."""
    forms = {value, urllib.parse.quote(value, safe=""), urllib.parse.quote_plus(value)}
    for prefix in ("", "Bearer "):
        forms.add(base64.b64encode((prefix + value).encode()).decode())
    return sorted(forms, key=len, reverse=True)


class Scrubber:
    """Replaces every known secret value with a fixed marker."""

    def __init__(self, secrets: Mapping[str, str]):
        self._needles: list[tuple[str, str]] = []
        for name, value in secrets.items():
            for form in _encodings(value):
                self._needles.append((form, f"[REDACTED:{name}]"))
        self._needles.sort(key=lambda t: len(t[0]), reverse=True)

    def scrub(self, text: str) -> str:
        for needle, marker in self._needles:
            if needle in text:
                text = text.replace(needle, marker)
        return text

    def scrub_obj(self, obj: Any) -> Any:
        if isinstance(obj, str):
            return self.scrub(obj)
        if isinstance(obj, bytes):
            return self.scrub(obj.decode("utf-8", "replace"))
        if isinstance(obj, Mapping):
            return {self.scrub(str(k)): self.scrub_obj(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [self.scrub_obj(v) for v in obj]
        return obj

    def contains_secret(self, text: str) -> bool:
        return any(needle in text for needle, _ in self._needles)


def mask(value: str) -> str:
    """Safe description of a matched secret for error output."""
    return f"{value[:4]}…({len(value)} chars)"
