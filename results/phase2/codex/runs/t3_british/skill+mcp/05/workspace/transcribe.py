#!/usr/bin/env python3
"""Transcribe a local recording with AssemblyAI's British English localisation."""

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
POLL_TIMEOUT = 3600  # Bound the wait for a queued/processing transcript.


def request_json(api_key, method, endpoint, data=None, content_type=None):
    headers = {"Authorization": api_key}
    if content_type:
        headers["Content-Type"] = content_type
    request = urllib.request.Request(
        BASE_URL + endpoint, data=data, headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"AssemblyAI HTTP {exc.code}: {detail}") from exc
    if not isinstance(result, dict):
        raise RuntimeError("AssemblyAI returned an unexpected JSON response")
    return result


def transcribe(audio_path, api_key):
    # Send raw audio bytes, rather than a JSON body or a path string.
    with audio_path.open("rb") as audio:
        uploaded = request_json(
            api_key, "POST", "/upload", audio, "application/octet-stream"
        )
    upload_url = uploaded.get("upload_url")
    if not isinstance(upload_url, str) or not upload_url:
        raise RuntimeError("AssemblyAI upload response has no upload_url")

    payload = {
        "audio_url": upload_url,
        "speech_models": ["universal-3-5-pro", "universal-2"],
        "language_detection": True,
        "language_detection_options": {
            "expected_languages": ["en"],
            "fallback_language": "en",
            "localization": ["en_uk"],
        },
    }
    transcript = request_json(
        api_key, "POST", "/transcript",
        json.dumps(payload).encode("utf-8"), "application/json",
    )
    transcript_id = transcript.get("id")
    if not isinstance(transcript_id, str) or not transcript_id:
        raise RuntimeError("AssemblyAI response has no transcript ID")

    deadline = time.monotonic() + POLL_TIMEOUT
    endpoint = "/transcript/" + urllib.parse.quote(transcript_id, safe="")
    while True:
        status = transcript.get("status")
        if status == "completed":
            if not isinstance(transcript.get("text"), str):
                raise RuntimeError("Completed transcript has no text")
            if transcript.get("language_code") != "en_uk":
                raise RuntimeError("AssemblyAI did not apply British English localisation")
            return {"transcript_id": transcript_id, "text": transcript["text"]}
        if status == "error":
            raise RuntimeError(
                f"Transcription {transcript_id} failed: {transcript.get('error')}"
            )
        if status not in ("queued", "processing"):
            raise RuntimeError(f"Unexpected transcription status: {status!r}")
        if time.monotonic() >= deadline:
            raise RuntimeError(f"Timed out waiting for transcript {transcript_id}")
        time.sleep(3)
        transcript = request_json(api_key, "GET", endpoint)


def write_output(path, result):
    # Replace the output only after success; failed writes leave no partial JSON.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=".transcript-", suffix=".json", delete=False,
        ) as output:
            temporary = Path(output.name)
            json.dump(result, output, ensure_ascii=False)
            output.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    api_key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("Set ASSEMBLYAI_API_KEY in the environment")
    if not args.audio.is_file() or args.audio.stat().st_size == 0:
        raise RuntimeError(f"Audio file is missing or empty: {args.audio}")
    if args.audio.resolve() == args.output.resolve():
        raise RuntimeError("Audio and output paths must be different")
    if not args.output.parent.is_dir():
        raise RuntimeError(f"Output directory does not exist: {args.output.parent}")
    write_output(args.output, transcribe(args.audio, api_key))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Transcription interrupted", file=sys.stderr)
        sys.exit(130)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
