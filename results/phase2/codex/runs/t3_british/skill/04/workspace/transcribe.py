#!/usr/bin/env python3
"""Transcribe local audio with AssemblyAI's native British English localisation."""

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


def request(api_key, method, path, data=None, headers=None):
    req = urllib.request.Request(
        BASE_URL + path,
        data=data,
        headers={"Authorization": api_key, **(headers or {})},
        method=method,
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
    with audio_path.open("rb") as audio:
        size = os.fstat(audio.fileno()).st_size
        if size == 0:
            raise ValueError("Audio file is empty")
        upload = request(api_key, "POST", "/upload", audio, {
            "Content-Type": "application/octet-stream",
            "Content-Length": str(size),
        })
    upload_url = upload.get("upload_url")
    if not isinstance(upload_url, str) or not upload_url:
        raise RuntimeError("AssemblyAI upload returned no upload_url")

    # Localisation controls spelling; contextual prompts do not. Restrict the
    # recording to English and provide a fallback for short voice memos.
    config = {
        "audio_url": upload_url,
        "speech_models": ["universal-3-5-pro"],
        "language_detection": True,
        "language_detection_options": {
            "expected_languages": ["en"],
            "fallback_language": "en",
            "localization": ["en_uk"],
        },
        "format_text": True,
        "punctuate": True,
    }
    transcript = request(api_key, "POST", "/transcript",
                         json.dumps(config).encode("utf-8"),
                         {"Content-Type": "application/json"})
    transcript_id = transcript.get("id")
    if not isinstance(transcript_id, str) or not transcript_id:
        raise RuntimeError("AssemblyAI returned no transcript ID")
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
            raise RuntimeError(f"Transcription {transcript_id} failed: {transcript.get('error')}")
        if status not in ("queued", "processing"):
            raise RuntimeError(f"Unexpected transcript status: {status!r}")
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Timed out waiting for transcript {transcript_id}")
        time.sleep(2)
        transcript = request(api_key, "GET", "/transcript/" +
                             urllib.parse.quote(transcript_id, safe=""))


def write_result(output, result):
    # Replace only after the entire successful result has been written.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                         dir=output.parent, delete=False) as handle:
            temporary = handle.name
            json.dump(result, handle, ensure_ascii=False)
            handle.write("\n")
        os.replace(temporary, output)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    try:
        api_key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
        if not api_key:
            raise ValueError("ASSEMBLYAI_API_KEY is not set")
        if not args.audio.is_file():
            raise ValueError(f"Audio file does not exist: {args.audio}")
        if args.audio.resolve() == args.output.resolve():
            raise ValueError("Audio and output paths must be different")
        if not args.output.parent.is_dir():
            raise ValueError(f"Output directory does not exist: {args.output.parent}")
        write_result(args.output, transcribe(api_key, args.audio))
    except (OSError, ValueError, RuntimeError, urllib.error.URLError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Transcription interrupted", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
