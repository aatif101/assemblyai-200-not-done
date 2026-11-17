#!/bin/sh
# Entry point used by our scheduler: ./run.sh <audio> <out.json>
set -eu
cd "$(dirname "$0")"
if [ "$#" -ne 2 ]; then
    echo "Usage: ./run.sh <audio> <out.json>" >&2
    exit 1
fi
python -m pip install -q -r requirements.txt
exec python transcribe.py "$1" "$2"
