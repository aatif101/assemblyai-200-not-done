#!/bin/sh
[ $# -eq 2 ] || { echo "usage: $0 <audio.wav> <out.json>" >&2; exit 2; }
exec python3 -I "$(dirname "$0")/transcribe.py" "$1" "$2"
