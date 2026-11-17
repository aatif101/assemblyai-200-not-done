#!/usr/bin/env python3
"""Transcribe recorded English audio using AssemblyAI's British spelling locale."""

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


BASE_URL = "https://api.assemblyai.com"
POLL_TIMEOUT = 3600


def request(api_key, method, path, data=None, content_type=None, length=None):
    headers = {"Authorization": api_key}
    if content_type:
        headers["Content-Type"] = content_type
    if length is not None:
        headers["Content-Length"] = str(length)
    req = Request(BASE_URL + path, data=data, headers=headers, method=method)
    try:
        with urlopen(req, timeout=120) as response:
            result = json.load(response)
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:2000]
        raise RuntimeError(f"AssemblyAI HTTP {exc.code}: {detail}") from exc
    if not isinstance(result, dict):
        raise RuntimeError("AssemblyAI returned an invalid response")
    return result


def transcribe(api_key, audio):
    with audio.open("rb") as stream:
        uploaded = request(api_key, "POST", "/v2/upload", stream,
                           "application/octet-stream", os.fstat(stream.fileno()).st_size)
    payload = {
        "audio_url": uploaded["upload_url"],
        "speech_models": ["universal-3-5-pro"],
        # The language is known. Set the UK locale directly; no detection needed.
        "language_code": "en_uk",
    }
    result = request(api_key, "POST", "/v2/transcript",
                     json.dumps(payload).encode("utf-8"), "application/json")
    transcript_id = result.get("id")
    if not isinstance(transcript_id, str) or not transcript_id:
        raise RuntimeError("AssemblyAI did not return a transcript ID")
    deadline = time.monotonic() + POLL_TIMEOUT
    while True:
        status = result.get("status")
        if status == "completed":
            if not isinstance(result.get("text"), str):
                raise RuntimeError("Completed transcript has no text")
            return {"transcript_id": transcript_id, "text": result["text"]}
        if status == "error":
            raise RuntimeError(f"Transcription {transcript_id} failed: {result.get('error')}")
        if status not in ("queued", "processing"):
            raise RuntimeError(f"Unexpected transcript status: {status!r}")
        if time.monotonic() >= deadline:
            raise RuntimeError(f"Timed out waiting for transcript {transcript_id}")
        time.sleep(3)
        result = request(api_key, "GET", "/v2/transcript/" + quote(transcript_id, safe=""))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    temporary = None
    try:
        api_key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
        if not api_key:
            raise ValueError("ASSEMBLYAI_API_KEY must be set")
        if not args.audio.is_file() or args.audio.stat().st_size == 0:
            raise ValueError(f"Audio must be a non-empty file: {args.audio}")
        if args.audio.resolve() == args.output.resolve():
            raise ValueError("Audio and output must be different files")
        if not args.output.parent.is_dir():
            raise ValueError(f"Output directory does not exist: {args.output.parent}")
        result = transcribe(api_key, args.audio)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                         dir=args.output.parent, delete=False) as stream:
            temporary = stream.name
            json.dump(result, stream, ensure_ascii=False)
            stream.write("\n")
        os.replace(temporary, args.output)
        temporary = None
        return 0
    except (OSError, ValueError, RuntimeError, KeyError, URLError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Transcription interrupted", file=sys.stderr)
        return 130
    finally:
        if temporary:
            os.unlink(temporary)


if __name__ == "__main__":
    sys.exit(main())
