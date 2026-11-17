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


BASE_URL = "https://api.assemblyai.com"


def request(api_key, method, path, data=None, content_type=None):
    headers = {"Authorization": api_key}
    if content_type:
        headers["Content-Type"] = content_type
    req = urllib.request.Request(
        BASE_URL + path, data=data, headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        # Never include credentials in diagnostics, even if echoed by a server.
        detail = detail.replace(api_key, "[redacted]")
        raise RuntimeError(f"AssemblyAI HTTP {exc.code}: {detail}") from None
    if not isinstance(result, dict):
        raise RuntimeError("AssemblyAI returned an invalid JSON response")
    return result


def transcribe(api_key, audio):
    with audio.open("rb") as stream:
        # Passing the file directly streams raw audio without loading it into RAM.
        upload = request(api_key, "POST", "/v2/upload", stream,
                         "application/octet-stream")
    upload_url = upload.get("upload_url")
    if not isinstance(upload_url, str) or not upload_url:
        raise RuntimeError("AssemblyAI upload response has no upload_url")

    config = {
        "audio_url": upload_url,
        "speech_models": ["universal-3-5-pro", "universal-2"],
        "language_detection": True,
        "language_detection_options": {
            "expected_languages": ["en"],
            "fallback_language": "en",
            "localization": ["en_uk"],
        },
    }
    transcript = request(api_key, "POST", "/v2/transcript",
                         json.dumps(config).encode("utf-8"), "application/json")
    transcript_id = transcript.get("id")
    if not isinstance(transcript_id, str) or not transcript_id:
        raise RuntimeError("AssemblyAI submission response has no transcript id")
    print(f"AssemblyAI transcript: {transcript_id}", file=sys.stderr)

    deadline = time.monotonic() + 3600
    while True:
        status = transcript.get("status")
        if status == "completed":
            text = transcript.get("text")
            if not isinstance(text, str):
                raise RuntimeError("Completed transcript has no text")
            return {"transcript_id": transcript_id, "text": text}
        if status == "error":
            raise RuntimeError(f"Transcription {transcript_id} failed: "
                               f"{transcript.get('error', 'unknown error')}")
        if status not in ("queued", "processing"):
            raise RuntimeError(f"Unexpected transcript status: {status!r}")
        if time.monotonic() >= deadline:
            raise RuntimeError(f"Timed out waiting for transcript {transcript_id}")
        time.sleep(2)
        transcript = request(api_key, "GET", "/v2/transcript/" +
                             urllib.parse.quote(transcript_id, safe=""))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    temporary_path = None
    api_key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
    try:
        if not api_key:
            raise ValueError("ASSEMBLYAI_API_KEY must be set")
        if not args.audio.is_file():
            raise ValueError(f"Audio file does not exist: {args.audio}")
        if args.audio.resolve() == args.output.resolve():
            raise ValueError("Audio and output must be different files")
        # Check output access before submitting a billable transcription.
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=args.output.parent,
            prefix=".transcript-", suffix=".json", delete=False
        ) as output:
            temporary_path = Path(output.name)
            result = transcribe(api_key, args.audio)
            json.dump(result, output, ensure_ascii=False)
            output.write("\n")
        os.replace(temporary_path, args.output)
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        message = str(exc)
        if api_key:
            message = message.replace(api_key, "[redacted]")
        print(f"Error: {message}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Transcription interrupted", file=sys.stderr)
        return 130
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
