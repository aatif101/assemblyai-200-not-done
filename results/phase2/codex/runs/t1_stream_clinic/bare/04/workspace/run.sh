#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONPATH="$SCRIPT_DIR/.vendor${PYTHONPATH:+:$PYTHONPATH}"
exec python3 "$SCRIPT_DIR/transcribe.py" "$@"
