"""F6: remove account identifiers from saved records (project_id, token_id, upload URLs).

The values are read from the records themselves, so they are never written into this script.
JSON and JSONL files are rewritten structurally so they stay valid. Re-runnable; prints how many files changed.
Grading does not read these fields.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"
KEYED = re.compile(r"""(project_id|token_id)\\?["']?\s*[:=]\s*(\d{4,})""")
UPLOAD = re.compile(r"(cdn\.assemblyai\.com/upload/)[A-Za-z0-9-]{8,}")
TEXT_SUFFIXES = {".json", ".jsonl", ".md", ".csv", ".txt", ".py", ".sh", ".log", ".js", ".ts", ".toml", ".yml", ".yaml"}


def text_files():
    for p in sorted(RESULTS.rglob("*")):
        if p.is_file() and p.suffix in TEXT_SUFFIXES:
            yield p


def main() -> int:
    values: dict[str, str] = {}
    for p in text_files():
        for m in KEYED.finditer(p.read_text(encoding="utf-8", errors="replace")):
            values[m.group(2)] = m.group(1)
    num = re.compile(r"(?<![\d.])(" + "|".join(map(re.escape, values)) + r")(?![\d.])") if values else None

    def fix_str(x: str) -> str:
        x = UPLOAD.sub(r"\1[REDACTED:upload_id]", x)
        return num.sub(lambda m: f"[REDACTED:{values[m.group(1)]}]", x) if num else x

    def walk(o):
        if isinstance(o, dict):
            return {k: walk(v) for k, v in o.items()}
        if isinstance(o, list):
            return [walk(v) for v in o]
        if isinstance(o, str):
            return fix_str(o)
        if isinstance(o, int) and not isinstance(o, bool) and str(o) in values:
            return f"[REDACTED:{values[str(o)]}]"
        return o

    changed = 0
    for p in text_files():
        try:
            s = p.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            continue
        if p.suffix == ".json":
            try:
                obj = json.loads(s)
            except json.JSONDecodeError:
                t = fix_str(s)
            else:
                new = walk(obj)
                t = s if new == obj else json.dumps(new, indent=2, ensure_ascii=False) + "\n"
        elif p.suffix == ".jsonl":
            out = []
            for line in s.split("\n"):
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    out.append(fix_str(line))
                    continue
                new = walk(obj)
                out.append(line if new == obj else json.dumps(new, ensure_ascii=False))
            t = "\n".join(out)
        else:
            t = fix_str(s)
        if t != s:
            p.write_bytes(t.encode("utf-8"))
            changed += 1
    print(f"identifier kinds found: {sorted(set(values.values()))}; files changed: {changed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
