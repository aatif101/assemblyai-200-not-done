#!/usr/bin/env python3
"""Transcribe a local recording with AssemblyAI's British spelling locale."""

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


def request_json(api_key, method, path, data=None, headers=None):
    request = urllib.request.Request(
        BASE_URL + path,
        data=data,
        headers={"Authorization": api_key, **(headers or {})},
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"AssemblyAI {method} {path}: HTTP {exc.code}: {detail}") from exc
    if not isinstance(result, dict):
        raise RuntimeError("AssemblyAI returned an invalid JSON response")
    return result


def transcribe(audio, api_key):
    with audio.open("rb") as recording:
        size = os.fstat(recording.fileno()).st_size
        if size == 0:
            raise ValueError("The audio file is empty")
        upload = request_json(
            api_key, "POST", "/upload", recording,
            {"Content-Type": "application/octet-stream", "Content-Length": str(size)},
        )
    upload_url = upload.get("upload_url")
    if not isinstance(upload_url, str) or not upload_url:
        raise RuntimeError("AssemblyAI did not return an upload URL")

    # A known English language needs the explicit UK locale. Context prompts
    # do not control spelling; en_uk applies AssemblyAI's native localisation.
    config = {
        "audio_url": upload_url,
        "speech_models": ["universal-3-5-pro"],
        "language_code": "en_uk",
        "punctuate": True,
        "format_text": True,
    }
    transcript = request_json(
        api_key, "POST", "/transcript", json.dumps(config).encode("utf-8"),
        {"Content-Type": "application/json"},
    )
    transcript_id = transcript.get("id")
    if not isinstance(transcript_id, str) or not transcript_id:
        raise RuntimeError(f"AssemblyAI did not return a transcript ID: {transcript.get('error', 'unknown error')}")

    deadline = time.monotonic() + 3600
    while True:
        status = transcript.get("status")
        if status == "completed":
            if not isinstance(transcript.get("text"), str):
                raise RuntimeError("Completed transcript has no text")
            return {"transcript_id": transcript_id, "text": transcript["text"]}
        if status == "error":
            raise RuntimeError(f"Transcription {transcript_id} failed: {transcript.get('error', 'unknown error')}")
        if status not in ("queued", "processing"):
            raise RuntimeError(f"Unexpected transcript status: {status!r}")
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Timed out waiting for transcript {transcript_id}")
        time.sleep(3)
        transcript = request_json(
            api_key, "GET", "/transcript/" + urllib.parse.quote(transcript_id, safe=""),
        )


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
        if args.audio.resolve() == args.output.resolve():
            raise ValueError("Audio and output paths must be different")
        # Check the destination before uploading; replace it only on success.
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=args.output.parent,
            prefix=".transcript-", suffix=".tmp", delete=False,
        ) as destination:
            temporary = Path(destination.name)
            result = transcribe(args.audio, api_key)
            json.dump(result, destination, ensure_ascii=False)
            destination.write("\n")
        os.replace(temporary, args.output)
        return 0
    except (OSError, ValueError, RuntimeError, urllib.error.URLError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
