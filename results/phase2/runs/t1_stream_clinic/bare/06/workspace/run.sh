#!/usr/bin/env bash
# Usage: ./run.sh <audio.wav> <out.json>
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$here/app/transcribe.py" "$@"
