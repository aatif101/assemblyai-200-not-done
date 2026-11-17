#!/bin/sh
# Entry point used by our scheduler: ./run.sh <audio> <out.json>
set -e
if [ "$#" -ne 2 ]; then
    echo "Usage: $0 <audio> <out.json>" >&2
    exit 2
fi
cd "$(dirname "$0")"
python -m pip install -q -r requirements.txt
exec python transcribe.py "$1" "$2"
