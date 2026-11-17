#!/usr/bin/env python3
"""Transcribe recorded audio with AssemblyAI's British English localisation."""

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
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"AssemblyAI HTTP {exc.code}: {detail}") from None
    if not isinstance(result, dict):
        raise RuntimeError("AssemblyAI returned an invalid response")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("Set ASSEMBLYAI_API_KEY in the environment")
    if args.audio.resolve() == args.output.resolve():
        raise RuntimeError("Audio and output paths must be different")
    if not args.output.parent.is_dir():
        raise RuntimeError("Output directory does not exist")

    with args.audio.open("rb") as audio:
        size = os.fstat(audio.fileno()).st_size
        if size == 0:
            raise RuntimeError("Audio file is empty")
        uploaded = request(key, "POST", "/upload", audio, "application/octet-stream", size)
    payload = {
        "audio_url": uploaded["upload_url"],
        "speech_models": ["universal-3-5-pro"],
        "language_detection": True,
        "language_detection_options": {
            "expected_languages": ["en"],
            "fallback_language": "en",
            "localization": ["en_uk"],
        },
    }
    transcript = request(key, "POST", "/transcript", json.dumps(payload).encode())
    transcript_id = transcript.get("id")
    if not isinstance(transcript_id, str) or not transcript_id:
        raise RuntimeError("AssemblyAI returned no transcript ID")
    deadline = time.monotonic() + 3600
    while transcript.get("status") != "completed":
        if transcript.get("status") == "error":
            raise RuntimeError(f"Transcript {transcript_id} failed: {transcript.get('error')}")
        if transcript.get("status") not in ("queued", "processing"):
            raise RuntimeError(f"Unexpected transcript status: {transcript.get('status')}")
        if time.monotonic() >= deadline:
            raise RuntimeError(f"Timed out waiting for transcript {transcript_id}")
        time.sleep(3)
        transcript = request(key, "GET", "/transcript/" + urllib.parse.quote(transcript_id, safe=""))
    if transcript.get("language_code") != "en_uk":
        raise RuntimeError(f"British English localisation was not applied to {transcript_id}")
    if not isinstance(transcript.get("text"), str):
        raise RuntimeError("Completed transcript has no text")

    # Publish only a complete result, without leaving partial JSON on failure.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=args.output.parent,
                                         prefix=".transcript-", suffix=".json", delete=False) as out:
            temporary = out.name
            json.dump({"transcript_id": transcript_id, "text": transcript["text"]}, out, ensure_ascii=False)
            out.write("\n")
        os.replace(temporary, args.output)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
