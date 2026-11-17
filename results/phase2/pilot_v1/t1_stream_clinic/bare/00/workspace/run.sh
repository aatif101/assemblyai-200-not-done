#!/bin/sh
# Usage: ./run.sh <audio.wav> <out.json>
exec python3 "$(dirname "$0")/transcribe.py" "$@"
