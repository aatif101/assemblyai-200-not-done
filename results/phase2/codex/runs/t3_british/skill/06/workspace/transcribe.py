#!/usr/bin/env python3
"""Transcribe recorded English audio with AssemblyAI's British localisation."""

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


def request(api_key, method, path, data=None, content_type="application/json"):
    headers = {"Authorization": api_key, "Content-Type": content_type}
    req = urllib.request.Request(
        BASE_URL + path, data=data, headers=headers, method=method
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


def transcribe(api_key, audio_path):
    # Stream raw audio bytes rather than loading the recording into memory.
    with audio_path.open("rb") as audio:
        upload = request(api_key, "POST", "/upload", audio, "application/octet-stream")
    if not isinstance(upload.get("upload_url"), str) or not upload["upload_url"]:
        raise RuntimeError("AssemblyAI upload response has no upload_url")

    payload = {
        "audio_url": upload["upload_url"],
        "speech_models": ["universal-3-5-pro", "universal-2"],
        "language_detection": True,
        "language_detection_options": {
            "expected_languages": ["en"],
            "fallback_language": "en",
            "localization": ["en_uk"],
        },
    }
    result = request(api_key, "POST", "/transcript", json.dumps(payload).encode())
    transcript_id = result.get("id")
    if not isinstance(transcript_id, str) or not transcript_id:
        raise RuntimeError("AssemblyAI submission response has no transcript id")
    print(f"AssemblyAI transcript: {transcript_id}", file=sys.stderr)
    deadline = time.monotonic() + 6 * 60 * 60
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
        result = request(api_key, "GET", "/transcript/" + urllib.parse.quote(transcript_id, safe=""))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    temp_path = None
    try:
        api_key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("ASSEMBLYAI_API_KEY must be set")
        if not args.audio.is_file() or args.audio.stat().st_size == 0:
            raise RuntimeError(f"Audio file is missing or empty: {args.audio}")
        if args.audio.resolve() == args.output.resolve():
            raise RuntimeError("Audio and output paths must differ")
        # Check the output location before incurring a transcription charge.
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=args.output.parent,
            prefix=".transcript-", suffix=".json", delete=False,
        ) as output:
            temp_path = Path(output.name)
            result = transcribe(api_key, args.audio)
            json.dump(result, output, ensure_ascii=False)
            output.write("\n")
        os.replace(temp_path, args.output)
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        # Do not expose the credential even if a server includes it in an error.
        message = str(exc)
        key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
        if key:
            message = message.replace(key, "[REDACTED]")
        print(f"Error: {message}", file=sys.stderr)
        return 1
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
