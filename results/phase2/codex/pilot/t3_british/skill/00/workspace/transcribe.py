#!/usr/bin/env python3
"""Transcribe a local recording using AssemblyAI's British English locale."""

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


API_BASE = "https://api.assemblyai.com/v2"
POLL_TIMEOUT = 3600


def request_json(api_key, method, endpoint, body=None, content_type=None):
    headers = {"Authorization": api_key, "Accept": "application/json"}
    if content_type:
        headers["Content-Type"] = content_type
    request = Request(API_BASE + endpoint, data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=120) as response:
            result = json.load(response)
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        # Avoid including credentials even if an upstream error echoes them.
        detail = detail.replace(api_key, "[REDACTED]")
        raise RuntimeError(f"AssemblyAI HTTP {exc.code}: {detail[:2000]}") from None
    except (URLError, TimeoutError) as exc:
        raise RuntimeError(f"AssemblyAI connection failed: {str(exc).replace(api_key, '[REDACTED]')}") from None
    if not isinstance(result, dict):
        raise RuntimeError("AssemblyAI returned an unexpected JSON response")
    return result


def transcribe(audio_path, api_key):
    # Upload raw bytes; file iteration lets urllib stream without loading the
    # entire recording into memory. WAV's header supplies its audio properties.
    with audio_path.open("rb") as audio:
        upload = request_json(api_key, "POST", "/upload", audio, "application/octet-stream")
    audio_url = upload.get("upload_url")
    if not isinstance(audio_url, str) or not audio_url:
        raise RuntimeError("AssemblyAI upload response is missing upload_url")

    config = {
        "audio_url": audio_url,
        "speech_models": ["universal-3-5-pro"],
        # Set the locale directly: these memos are known to be English, and
        # language detection is unreliable for clips shorter than 15 seconds.
        "language_code": "en_uk",
        "language_detection": False,
        "punctuate": True,
        "format_text": True,
    }
    result = request_json(
        api_key, "POST", "/transcript", json.dumps(config).encode(), "application/json"
    )
    transcript_id = result.get("id")
    if not isinstance(transcript_id, str) or not transcript_id:
        raise RuntimeError(f"AssemblyAI did not return a transcript ID: {result.get('error', 'unknown error')}")
    print(f"AssemblyAI transcript: {transcript_id}", file=sys.stderr)
    deadline = time.monotonic() + POLL_TIMEOUT
    while True:
        status = result.get("status")
        if status == "completed":
            text = result.get("text")
            if not isinstance(text, str):
                raise RuntimeError("Completed transcript is missing its text")
            return {"transcript_id": transcript_id, "text": text}
        if status == "error":
            raise RuntimeError(f"Transcription {transcript_id} failed: {result.get('error', 'unknown error')}")
        if status not in ("queued", "processing"):
            raise RuntimeError(f"Unexpected transcript status: {status!r}")
        if time.monotonic() >= deadline:
            raise RuntimeError(f"Timed out waiting for transcript {transcript_id}")
        time.sleep(3)
        result = request_json(api_key, "GET", "/transcript/" + quote(transcript_id, safe=""))


def write_output(path, result):
    # Replace only after successful transcription and a complete JSON write.
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".transcript-", suffix=".json", delete=False) as output:
            temporary_path = Path(output.name)
            json.dump(result, output, ensure_ascii=False)
            output.write("\n")
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    try:
        api_key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
        if not api_key:
            raise ValueError("ASSEMBLYAI_API_KEY must be set")
        if not args.audio.is_file() or args.audio.stat().st_size == 0:
            raise ValueError(f"Audio file does not exist or is empty: {args.audio}")
        if args.audio.resolve() == args.output.resolve():
            raise ValueError("Output path must differ from the audio path")
        if not args.output.parent.is_dir():
            raise ValueError(f"Output directory does not exist: {args.output.parent}")
        write_output(args.output, transcribe(args.audio, api_key))
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Transcription interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
