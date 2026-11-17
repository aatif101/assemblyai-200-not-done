#!/usr/bin/env python3
"""Fail if a credential appears anywhere in the repository.

Two independent checks over every file git would commit (tracked + untracked, minus
.gitignore), plus everything under results/ whether or not it is ignored:

  1. exact values   the value of every secret-looking environment variable and of every
                    entry in .env (ASSEMBLYAI_API_KEY, ANTHROPIC_API_KEY, *_TOKEN, ...),
                    including URL-encoded and base64 forms. Checked in every file, as bytes.
  2. key shapes     32-hex strings, sk-ant-..., "Bearer <token>", JWTs. Checked in text files.

Exit status 1 on any hit. Matches are reported masked; the secret itself is never printed.
A reviewed false positive can be listed (one literal per line) in .secrets-allowlist.

Usage:  python scripts/check_secrets.py [--staged] [--root DIR]

--root scans another directory (an exported copy) with this repo's .env and allowlist.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from twohundred.secrets import SECRET_PATTERNS, _encodings, mask, secret_values  # noqa: E402

SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", ".pytest_cache"}
NEVER_SCAN = {".env"}  # holds the secrets by design and is git-ignored
MAX_TEXT_BYTES = 8 * 1024 * 1024


def candidate_files(staged: bool, root: Path = ROOT) -> list[Path]:
    cmd = (["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"] if staged
           else ["git", "ls-files", "--cached", "--others", "--exclude-standard"])
    try:
        if root != ROOT:
            raise FileNotFoundError  # an export: walk all of it
        out = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=True).stdout
        files = {ROOT / line for line in out.splitlines() if line.strip()}
    except (subprocess.CalledProcessError, FileNotFoundError):
        files = set()
    if not staged:
        # Not a git repo, or files hidden by .gitignore: walk results/ and the tree regardless.
        walk_all = not files
        for base in (root / "results", root):
            if base.is_dir():
                for path in base.rglob("*"):
                    if path.is_file() and not (set(path.relative_to(root).parts) & SKIP_DIRS):
                        if base == root / "results" or walk_all:
                            files.add(path)
    return sorted(p for p in files if p.is_file() and p.name not in NEVER_SCAN)


def main() -> int:
    staged = "--staged" in sys.argv
    root = Path(sys.argv[sys.argv.index("--root") + 1]).resolve() if "--root" in sys.argv else ROOT
    secrets = secret_values(dotenv=ROOT / ".env")
    allow_file = ROOT / ".secrets-allowlist"
    allow = {line.strip() for line in allow_file.read_text().splitlines()
             if line.strip() and not line.startswith("#")} if allow_file.is_file() else set()

    needles: list[tuple[bytes, str]] = []
    for name, value in secrets.items():
        for form in _encodings(value):
            needles.append((form.encode(), name))

    hits: list[str] = []
    files = candidate_files(staged, root)
    for path in files:
        rel = path.relative_to(root).as_posix()
        data = path.read_bytes()
        for needle, name in needles:
            if needle in data:
                line = data[: data.index(needle)].count(b"\n") + 1
                hits.append(f"{rel}:{line}: contains the value of ${name}")
        if len(data) > MAX_TEXT_BYTES or b"\x00" in data[:4096]:
            continue  # binary: exact-value check only
        text = data.decode("utf-8", "replace")
        for label, pattern in SECRET_PATTERNS.items():
            for m in pattern.finditer(text):
                if m.group(0) in allow:
                    continue
                line = text.count("\n", 0, m.start()) + 1
                hits.append(f"{rel}:{line}: looks like a credential ({label}): {mask(m.group(0))}")

    known = ", ".join(sorted(secrets)) or "none set in this shell"
    if hits:
        print("SECRET CHECK FAILED", file=sys.stderr)
        for h in hits:
            print("  " + h, file=sys.stderr)
        print("\nRemove the value (or, for a reviewed false positive, add the literal to "
              ".secrets-allowlist) and re-run.", file=sys.stderr)
        return 1
    print(f"secret check passed: {len(files)} files scanned; exact-value check for: {known}")
    if "ASSEMBLYAI_API_KEY" not in secrets:
        print("  note: ASSEMBLYAI_API_KEY is not set here, so only the key-shape patterns were "
              "checked for it. Run this in the shell (or with the .env) that holds the key.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
