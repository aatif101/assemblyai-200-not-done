#!/bin/sh
# Entry point used by our scheduler: ./run.sh <audio> <out.json>
set -e
cd "$(dirname "$0")"
python -m pip install -q -r requirements.txt
exec python transcribe.py "$1" "$2"
