#!/bin/sh
# Entry point used by our scheduler: ./run.sh <audio> <out.json>
set -e
if [ "$#" -ne 2 ]; then
    echo "Usage: ./run.sh <audio> <out.json>" >&2
    exit 1
fi
cd "$(dirname "$0")"
python3 -m pip install -q -r requirements.txt
exec python3 transcribe.py "$1" "$2"
