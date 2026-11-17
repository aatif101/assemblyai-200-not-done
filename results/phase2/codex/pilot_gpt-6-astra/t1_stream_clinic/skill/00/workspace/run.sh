#!/bin/sh
set -eu
BASE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec "$BASE/.venv/bin/python" "$BASE/transcribe.py" "$@"
