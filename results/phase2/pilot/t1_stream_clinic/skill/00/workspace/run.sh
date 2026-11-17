#!/bin/sh
exec python3 "$(dirname "$0")/app/transcribe.py" "$@"
