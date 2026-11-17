#!/usr/bin/env python3
"""Transcribe local audio with AssemblyAI's British English spelling."""

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


BASE_URL = "https://api.assemblyai.com/v2"


def request_json(api_key, method, endpoint, data=None, headers=None, timeout=120):
    request = Request(
        BASE_URL + endpoint,
        data=data,
        headers={"Authorization": api_key, **(headers or {})},
        method=method,
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            result = json.load(response)
    except HTTPError as exc:
        detail = exc.read(4096).decode("utf-8", errors="replace")
        raise RuntimeError(f"AssemblyAI HTTP {exc.code}: {detail}") from None
    if not isinstance(result, dict):
        raise RuntimeError("AssemblyAI returned an invalid JSON response")
    return result


def transcribe(audio_path, api_key):
    with audio_path.open("rb") as audio:
        size = os.fstat(audio.fileno()).st_size
        if size == 0:
            raise ValueError("Audio file is empty")
        upload = request_json(
            api_key, "POST", "/upload", data=audio,
            headers={"Content-Type": "application/octet-stream", "Content-Length": str(size)},
            timeout=300,
        )
    if not isinstance(upload.get("upload_url"), str) or not upload["upload_url"]:
        raise RuntimeError("AssemblyAI did not return an upload URL")

    # The language is known: en_uk selects British spelling directly.
    # A contextual prompt does not control spelling or formatting.
    payload = {
        "audio_url": upload["upload_url"],
        "speech_models": ["universal-3-5-pro"],
        "language_code": "en_uk",
        "language_detection": False,
    }
    result = request_json(
        api_key, "POST", "/transcript", data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
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
            raise RuntimeError(f"Transcription {transcript_id} failed: {result.get('error', 'unknown error')}")
        if status not in ("queued", "processing"):
            raise RuntimeError(f"Unexpected transcript status: {status!r}")
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Timed out waiting for transcript {transcript_id}")
        time.sleep(3)
        result = request_json(api_key, "GET", "/transcript/" + quote(transcript_id, safe=""))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    api_key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
    if not api_key:
        raise ValueError("ASSEMBLYAI_API_KEY must be set")
    if args.audio.resolve() == args.output.resolve():
        raise ValueError("Output path must differ from the audio path")
    if not args.output.parent.is_dir():
        raise ValueError(f"Output directory does not exist: {args.output.parent}")

    result = transcribe(args.audio, api_key)
    # Replace atomically so an unsuccessful run cannot leave partial JSON.
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=args.output.parent, delete=False) as output:
            temp_path = output.name
            json.dump(result, output, ensure_ascii=False)
            output.write("\n")
        os.replace(temp_path, args.output)
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)


if __name__ == "__main__":
    try:
        main()
    except (OSError, URLError, ValueError, RuntimeError) as exc:
        message = str(exc)
        key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
        if key:
            message = message.replace(key, "[REDACTED]")
        print(f"Error: {message}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("Transcription interrupted", file=sys.stderr)
        sys.exit(130)
