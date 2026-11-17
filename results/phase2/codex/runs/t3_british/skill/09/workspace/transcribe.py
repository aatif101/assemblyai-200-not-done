#!/usr/bin/env python3
"""Transcribe local recordings with AssemblyAI's British English locale."""

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


API = "https://api.assemblyai.com/v2"


def request(key, method, path, data=None, headers=None):
    req = Request(
        API + path,
        data=data,
        headers={"Authorization": key, **(headers or {})},
        method=method,
    )
    try:
        with urlopen(req, timeout=120) as response:
            result = json.load(response)
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"AssemblyAI HTTP {exc.code}: {detail}") from exc
    if not isinstance(result, dict):
        raise RuntimeError("AssemblyAI returned an invalid response")
    return result


def transcribe(key, audio):
    with audio.open("rb") as stream:
        size = os.fstat(stream.fileno()).st_size
        if size == 0:
            raise ValueError("Audio file is empty")
        upload = request(key, "POST", "/upload", stream, {
            "Content-Type": "application/octet-stream",
            "Content-Length": str(size),
        })
    payload = {
        "audio_url": upload["upload_url"],
        "speech_models": ["universal-3-5-pro"],
        # Set the output locale explicitly: an English accent alone does not
        # select British spelling, and a contextual prompt cannot enforce it.
        "language_code": "en_uk",
    }
    result = request(key, "POST", "/transcript",
                     json.dumps(payload).encode("utf-8"),
                     {"Content-Type": "application/json"})
    transcript_id = result.get("id")
    if not isinstance(transcript_id, str) or not transcript_id:
        raise RuntimeError("AssemblyAI did not return a transcript ID")
    deadline = time.monotonic() + 3600
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
        result = request(key, "GET", "/transcript/" + quote(transcript_id, safe=""))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
    temporary = None
    try:
        if not key:
            raise ValueError("ASSEMBLYAI_API_KEY is not set")
        if args.audio.resolve() == args.output.resolve():
            raise ValueError("Audio and output paths must be different")
        if not args.output.parent.is_dir():
            raise ValueError("Output directory does not exist")
        result = transcribe(key, args.audio)
        # Publish only a complete JSON document, preserving existing output
        # if transcription or writing fails.
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                         dir=args.output.parent, delete=False) as stream:
            temporary = stream.name
            json.dump(result, stream, ensure_ascii=False)
            stream.write("\n")
        os.replace(temporary, args.output)
        temporary = None
        return 0
    except (OSError, ValueError, KeyError, RuntimeError, URLError) as exc:
        print(f"Error: {str(exc).replace(key, '[redacted]') if key else exc}", file=sys.stderr)
        return 1
    finally:
        if temporary is not None:
            os.unlink(temporary)


if __name__ == "__main__":
    sys.exit(main())
