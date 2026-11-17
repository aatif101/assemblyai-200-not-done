#!/usr/bin/env python3
"""Transcribe local recordings with AssemblyAI's British English spelling."""

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


BASE_URL = "https://api.assemblyai.com"


def request_json(api_key, method, path, data=None, headers=None):
    request = Request(
        BASE_URL + path,
        data=data,
        headers={"Authorization": api_key, **(headers or {})},
        method=method,
    )
    try:
        with urlopen(request, timeout=120) as response:
            result = json.load(response)
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"AssemblyAI {method} {path}: HTTP {exc.code}: {detail}") from None
    if not isinstance(result, dict):
        raise RuntimeError("AssemblyAI returned an unexpected response")
    return result


def transcribe(api_key, audio_path):
    with audio_path.open("rb") as audio:
        size = os.fstat(audio.fileno()).st_size
        if not size:
            raise ValueError("Audio file is empty")
        uploaded = request_json(
            api_key, "POST", "/v2/upload", data=audio,
            headers={"Content-Type": "application/octet-stream", "Content-Length": str(size)},
        )

    # Use the native locale, which controls spelling across the transcript.
    # No language detection is needed for these known English voice memos.
    config = {
        "audio_url": uploaded["upload_url"],
        "speech_models": ["universal-3-5-pro"],
        "language_code": "en_uk",
        "punctuate": True,
        "format_text": True,
    }
    result = request_json(
        api_key, "POST", "/v2/transcript", data=json.dumps(config).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    transcript_id = result.get("id")
    if not isinstance(transcript_id, str) or not transcript_id:
        raise RuntimeError(f"AssemblyAI did not return a transcript id: {result}")
    print(f"AssemblyAI transcript: {transcript_id}", file=sys.stderr)
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
            raise TimeoutError(f"Timed out waiting for transcript {transcript_id}")
        time.sleep(3)
        result = request_json(api_key, "GET", "/v2/transcript/" + quote(transcript_id, safe=""))


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
    result = transcribe(api_key, args.audio)
    # Publish a complete JSON document atomically, only after success.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=args.output.parent, delete=False,
        ) as output:
            temporary = output.name
            json.dump(result, output, ensure_ascii=False)
            output.write("\n")
        os.replace(temporary, args.output)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


if __name__ == "__main__":
    try:
        main()
    except (OSError, URLError, ValueError, KeyError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        sys.exit(130)
