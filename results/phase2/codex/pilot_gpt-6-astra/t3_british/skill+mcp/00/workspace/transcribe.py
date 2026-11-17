#!/usr/bin/env python3
"""Transcribe English recordings using AssemblyAI's British spelling locale."""

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

BASE_URL = "https://api.assemblyai.com/v2"


def request(api_key, method, path, data=None, content_type="application/json", size=None):
    headers = {"Authorization": api_key, "Content-Type": content_type}
    if size is not None:
        headers["Content-Length"] = str(size)
    req = Request(BASE_URL + path, data=data, headers=headers, method=method)
    try:
        with urlopen(req, timeout=120) as response:
            result = json.load(response)
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"AssemblyAI HTTP {exc.code}: {detail}") from None
    if not isinstance(result, dict):
        raise RuntimeError("AssemblyAI returned an unexpected response")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    api_key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("ASSEMBLYAI_API_KEY must be set")
    if args.audio.resolve() == args.output.resolve():
        raise RuntimeError("Audio and output paths must be different")
    if not args.output.parent.is_dir():
        raise RuntimeError("Output directory does not exist")

    with args.audio.open("rb") as audio:
        size = os.fstat(audio.fileno()).st_size
        if not size:
            raise RuntimeError("Audio file is empty")
        upload = request(api_key, "POST", "/upload", audio,
                         "application/octet-stream", size)

    # Known English audio: the explicit locale controls regional spelling.
    # https://www.assemblyai.com/docs/pre-recorded-audio/language-detection#choose-a-locale
    payload = {
        "audio_url": upload["upload_url"],
        "speech_models": ["universal-3-5-pro"],
        "language_code": "en_uk",
    }
    transcript = request(api_key, "POST", "/transcript", json.dumps(payload).encode())
    transcript_id = transcript.get("id")
    if not isinstance(transcript_id, str) or not transcript_id:
        raise RuntimeError("AssemblyAI did not return a transcript id")
    deadline = time.monotonic() + 3600
    while transcript.get("status") != "completed":
        if transcript.get("status") == "error":
            raise RuntimeError(f"Transcription {transcript_id} failed: {transcript.get('error')}")
        if transcript.get("status") not in ("queued", "processing"):
            raise RuntimeError(f"Unexpected transcription status: {transcript.get('status')}")
        if time.monotonic() >= deadline:
            raise RuntimeError(f"Timed out waiting for transcript {transcript_id}")
        time.sleep(3)
        transcript = request(api_key, "GET", "/transcript/" + quote(transcript_id, safe=""))

    if not isinstance(transcript.get("text"), str):
        raise RuntimeError("Completed transcript has no text string")
    result = {"transcript_id": transcript_id, "text": transcript["text"]}
    # Replace the destination only after a complete, successful response.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=args.output.parent,
                                         delete=False) as out:
            temporary = out.name
            json.dump(result, out, ensure_ascii=False)
            out.write("\n")
        os.replace(temporary, args.output)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Transcription interrupted", file=sys.stderr)
        sys.exit(130)
    except Exception as exc:
        message = str(exc)
        key = os.environ.get("ASSEMBLYAI_API_KEY")
        if key:
            message = message.replace(key, "[redacted]")
        print(f"Error: {message}", file=sys.stderr)
        sys.exit(1)
