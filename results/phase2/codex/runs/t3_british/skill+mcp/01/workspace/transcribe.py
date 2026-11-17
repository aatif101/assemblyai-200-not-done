#!/usr/bin/env python3
"""Transcribe recorded English audio with AssemblyAI's British spelling locale."""

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


def request(api_key, method, path, *, data=None, headers=None):
    req = urllib.request.Request(
        BASE_URL + path,
        method=method,
        data=data,
        headers={"Authorization": api_key, **(headers or {})},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"AssemblyAI {method} {path}: HTTP {exc.code}: {detail}") from exc
    if not isinstance(result, dict):
        raise RuntimeError("AssemblyAI returned an invalid JSON response")
    return result


def transcribe(audio_path, api_key):
    with audio_path.open("rb") as audio:
        size = os.fstat(audio.fileno()).st_size
        if size == 0:
            raise ValueError("Audio file is empty")
        upload = request(
            api_key, "POST", "/upload", data=audio,
            headers={"Content-Type": "application/octet-stream", "Content-Length": str(size)},
        )
    if not isinstance(upload.get("upload_url"), str) or not upload["upload_url"]:
        raise RuntimeError("AssemblyAI upload response is missing upload_url")

    # en_uk controls regional spelling directly for known English audio.
    # A contextual prompt does not control spelling or formatting.
    config = {
        "audio_url": upload["upload_url"],
        "speech_models": ["universal-3-5-pro"],
        "language_code": "en_uk",
        "language_detection": False,
    }
    result = request(
        api_key, "POST", "/transcript", data=json.dumps(config).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    transcript_id = result.get("id")
    if not isinstance(transcript_id, str) or not transcript_id:
        raise RuntimeError("AssemblyAI submission response is missing transcript id")
    endpoint = "/transcript/" + urllib.parse.quote(transcript_id, safe="")
    deadline = time.monotonic() + 3600
    while True:
        status = result.get("status")
        if status == "completed":
            if not isinstance(result.get("text"), str):
                raise RuntimeError("Completed transcript is missing text")
            return {"transcript_id": transcript_id, "text": result["text"]}
        if status == "error":
            raise RuntimeError(f"Transcription {transcript_id} failed: {result.get('error')}")
        if status not in ("queued", "processing"):
            raise RuntimeError(f"Unexpected transcription status: {status!r}")
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Timed out waiting for transcript {transcript_id}")
        time.sleep(3)
        result = request(api_key, "GET", endpoint)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    api_key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
    if not api_key:
        raise ValueError("ASSEMBLYAI_API_KEY environment variable is required")
    if args.audio.resolve() == args.output.resolve():
        raise ValueError("Audio and output paths must be different")
    if not args.output.parent.is_dir():
        raise ValueError("Output directory does not exist")
    result = transcribe(args.audio, api_key)
    # Replace the output atomically, so failures never leave partial JSON.
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=args.output.parent, delete=False
        ) as output:
            temp_path = Path(output.name)
            json.dump(result, output, ensure_ascii=False)
            output.write("\n")
        os.replace(temp_path, args.output)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Transcription interrupted", file=sys.stderr)
        sys.exit(130)
    except Exception as exc:
        # Never include the API key in diagnostics, even if a server echoes it.
        message = str(exc)
        key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
        if key:
            message = message.replace(key, "[REDACTED]")
        print(f"Error: {message}", file=sys.stderr)
        sys.exit(1)
