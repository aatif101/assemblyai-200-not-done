#!/usr/bin/env python3
"""Transcribe a local recording with AssemblyAI's British English locale."""

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


def request(api_key, method, endpoint, data=None, headers=None):
    req = Request(
        BASE_URL + endpoint,
        data=data,
        method=method,
        headers={"Authorization": api_key, **(headers or {})},
    )
    try:
        with urlopen(req, timeout=120) as response:
            result = json.load(response)
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"AssemblyAI {method} {endpoint}: HTTP {exc.code}: {detail}") from exc
    except (URLError, TimeoutError) as exc:
        raise RuntimeError(f"AssemblyAI {method} {endpoint}: {exc}") from exc
    if not isinstance(result, dict):
        raise RuntimeError("AssemblyAI returned an unexpected response")
    return result


def transcribe(audio_path, api_key):
    with audio_path.open("rb") as audio:
        size = os.fstat(audio.fileno()).st_size
        if size == 0:
            raise ValueError("Audio file is empty")
        uploaded = request(api_key, "POST", "/upload", audio, {
            "Content-Type": "application/octet-stream",
            "Content-Length": str(size),
        })

    # Explicit locale applies British spelling even for short English memos;
    # automatic language detection is unnecessary for this English-only workflow.
    payload = {
        "audio_url": uploaded["upload_url"],
        "speech_models": ["universal-3-5-pro"],
        "language_code": "en_uk",
    }
    transcript = request(
        api_key, "POST", "/transcript", json.dumps(payload).encode("utf-8"),
        {"Content-Type": "application/json"},
    )
    transcript_id = transcript.get("id")
    if not isinstance(transcript_id, str) or not transcript_id:
        raise RuntimeError("AssemblyAI did not return a transcript ID")
    deadline = time.monotonic() + 3600
    while True:
        status = transcript.get("status")
        if status == "completed":
            text = transcript.get("text")
            if not isinstance(text, str):
                raise RuntimeError("Completed transcript has no text")
            return {"transcript_id": transcript_id, "text": text}
        if status == "error":
            raise RuntimeError(f"Transcript {transcript_id} failed: {transcript.get('error')}")
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
    temporary_path = None
    try:
        api_key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
        if not api_key:
            raise ValueError("ASSEMBLYAI_API_KEY is not set")
        if args.audio.resolve() == args.output.resolve():
            raise ValueError("Output path must differ from the audio path")
        if not args.audio.is_file():
            raise ValueError(f"Audio file does not exist: {args.audio}")
        if not args.output.parent.is_dir():
            raise ValueError(f"Output directory does not exist: {args.output.parent}")
        result = transcribe(args.audio, api_key)
        # Publish only a complete JSON result, leaving existing output intact on failure.
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=args.output.parent,
            prefix=".transcript-", suffix=".json", delete=False,
        ) as output:
            temporary_path = Path(output.name)
            json.dump(result, output, ensure_ascii=False)
            output.write("\n")
        os.replace(temporary_path, args.output)
        return 0
    except Exception as exc:
        message = str(exc)
        if api_key:
            message = message.replace(api_key, "[REDACTED]")
        print(f"Error: {message}", file=sys.stderr)
        return 1
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
