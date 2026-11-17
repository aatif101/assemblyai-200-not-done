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


BASE_URL = "https://api.assemblyai.com/v2"
POLL_TIMEOUT = 7200  # Bound the wait for a queued or processing transcript.


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
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"AssemblyAI {method} {path}: HTTP {exc.code}: {detail}") from None
    except URLError as exc:
        raise RuntimeError(f"AssemblyAI connection failed: {exc.reason}") from None
    if not isinstance(result, dict):
        raise RuntimeError("AssemblyAI returned an unexpected response")
    return result


def transcribe(api_key, audio):
    with audio.open("rb") as source:
        upload = request(api_key, "POST", "/upload", source,
                         "application/octet-stream", os.fstat(source.fileno()).st_size)
    upload_url = upload.get("upload_url")
    if not isinstance(upload_url, str) or not upload_url:
        raise RuntimeError("AssemblyAI upload response has no upload_url")

    # Known English audio: select the regional spelling directly, without
    # language detection or a formatting instruction in the contextual prompt.
    payload = {
        "audio_url": upload_url,
        "speech_models": ["universal-3-5-pro"],
        "language_code": "en_uk",
    }
    transcript = request(api_key, "POST", "/transcript",
                         json.dumps(payload).encode("utf-8"), "application/json")
    transcript_id = transcript.get("id")
    if not isinstance(transcript_id, str) or not transcript_id:
        raise RuntimeError("AssemblyAI submission response has no transcript id")

    deadline = time.monotonic() + POLL_TIMEOUT
    while True:
        status = transcript.get("status")
        if status == "completed":
            text = transcript.get("text")
            if not isinstance(text, str):
                raise RuntimeError("Completed transcript has no text")
            return {"transcript_id": transcript_id, "text": text}
        if status == "error":
            raise RuntimeError(f"Transcription {transcript_id} failed: {transcript.get('error')}")
        if status not in ("queued", "processing"):
            raise RuntimeError(f"Unexpected transcript status: {status!r}")
        if time.monotonic() >= deadline:
            raise RuntimeError(f"Timed out waiting for transcript {transcript_id}")
        time.sleep(3)
        transcript = request(api_key, "GET", "/transcript/" + quote(transcript_id, safe=""))


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
            raise RuntimeError("Audio and output paths must be different")
        # Check the destination before making a billable API request. Replace
        # atomically on success so failures cannot leave partial JSON behind.
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                         dir=args.output.parent, delete=False) as output:
            temp_path = Path(output.name)
            result = transcribe(api_key, args.audio)
            json.dump(result, output, ensure_ascii=False)
            output.write("\n")
        os.replace(temp_path, args.output)
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        message = str(exc)
        if api_key:
            message = message.replace(api_key, "[REDACTED]")
        print(f"Error: {message}", file=sys.stderr)
        return 1
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
