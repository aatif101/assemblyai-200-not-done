#!/usr/bin/env bash
set -euo pipefail
umask 077
exec python3 "$(dirname "$(realpath "$0")")/transcribe.py" "$@"
