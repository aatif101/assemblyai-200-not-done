#!/usr/bin/env python3
"""Transcribe local audio with AssemblyAI's native British English spelling."""

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request


BASE_URL = "https://api.assemblyai.com/v2"


def request(api_key, method, path, data=None, content_type="application/json", size=None):
    headers = {"Authorization": api_key, "Content-Type": content_type}
    if size is not None:
        headers["Content-Length"] = str(size)
    req = urllib.request.Request(BASE_URL + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"AssemblyAI HTTP {exc.code}: {detail}") from exc


def transcribe(api_key, audio):
    with audio.open("rb") as stream:
        upload = request(api_key, "POST", "/upload", stream,
                         "application/octet-stream", audio.stat().st_size)
    config = {
        "audio_url": upload["upload_url"],
        "speech_models": ["universal-3-5-pro"],
        "language_detection": True,
        "language_detection_options": {
            "expected_languages": ["en"],
            "fallback_language": "en",
            "localization": ["en_uk"],
        },
    }
    result = request(api_key, "POST", "/transcript", json.dumps(config).encode("utf-8"))
    transcript_id = result["id"]
    print(f"AssemblyAI transcript: {transcript_id}", file=sys.stderr)
    deadline = time.monotonic() + 7200
    while result.get("status") != "completed":
        if result.get("status") == "error":
            raise RuntimeError(f"Transcription {transcript_id} failed: {result.get('error')}")
        if result.get("status") not in ("queued", "processing"):
            raise RuntimeError(f"Unexpected transcript status: {result.get('status')!r}")
        if time.monotonic() >= deadline:
            raise RuntimeError(f"Timed out waiting for transcript {transcript_id}")
        time.sleep(3)
        result = request(api_key, "GET", "/transcript/" + urllib.parse.quote(transcript_id, safe=""))
    if not isinstance(result.get("text"), str):
        raise RuntimeError("AssemblyAI completed without transcript text")
    return {"transcript_id": transcript_id, "text": result["text"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    api_key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("ASSEMBLYAI_API_KEY must be set")
    if not args.audio.is_file() or args.audio.stat().st_size == 0:
        raise RuntimeError(f"Audio file is missing or empty: {args.audio}")
    if args.audio.resolve() == args.output.resolve():
        raise RuntimeError("Audio and output paths must be different")
    if not args.output.parent.is_dir():
        raise RuntimeError(f"Output directory does not exist: {args.output.parent}")
    result = transcribe(api_key, args.audio)
    # Replace only after a complete result has been safely written.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=args.output.parent,
                                         prefix=".transcript-", delete=False) as stream:
            temporary = stream.name
            json.dump(result, stream, ensure_ascii=False)
            stream.write("\n")
        os.replace(temporary, args.output)
    finally:
        if temporary is not None and os.path.exists(temporary):
            os.unlink(temporary)


if __name__ == "__main__":
    try:
        main()
    except (Exception, KeyboardInterrupt) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
