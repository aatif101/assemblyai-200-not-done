#!/usr/bin/env bash
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$here/app/.venv/bin/python" -I "$here/app/transcribe.py" "$@"
