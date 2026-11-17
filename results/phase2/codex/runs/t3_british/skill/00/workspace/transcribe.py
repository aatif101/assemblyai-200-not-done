#!/usr/bin/env python3
"""Transcribe recorded audio with AssemblyAI's British English localisation."""

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
        raise RuntimeError(f"AssemblyAI HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"AssemblyAI connection failed: {exc.reason}") from exc
    if not isinstance(result, dict):
        raise RuntimeError("AssemblyAI returned an invalid response")
    return result


def transcribe(api_key, audio):
    with audio.open("rb") as source:
        upload = request(api_key, "POST", "/upload", source, {
            "Content-Type": "application/octet-stream",
            "Content-Length": str(os.fstat(source.fileno()).st_size),
        })
    config = {
        "audio_url": upload["upload_url"],
        "speech_models": ["universal-3-5-pro"],
        "language_detection": True,
        "language_detection_options": {
            "expected_languages": ["en"],
            "fallback_language": "en",
            "localization": ["en_uk"],
        },
    }
    result = request(api_key, "POST", "/transcript",
                     json.dumps(config).encode("utf-8"),
                     {"Content-Type": "application/json"})
    transcript_id = result.get("id")
    if not isinstance(transcript_id, str) or not transcript_id:
        raise RuntimeError("AssemblyAI did not return a transcript ID")

    deadline = time.monotonic() + 3600
    while True:
        status = result.get("status")
        if status == "completed":
            break
        if status == "error":
            raise RuntimeError(f"Transcript {transcript_id} failed: {result.get('error')}")
        if status not in ("queued", "processing"):
            raise RuntimeError(f"Unexpected transcript status: {status!r}")
        if time.monotonic() >= deadline:
            raise RuntimeError(f"Timed out waiting for transcript {transcript_id}")
        time.sleep(3)
        result = request(api_key, "GET", "/transcript/" + quote(transcript_id, safe=""))

    for warning in (result.get("metadata") or {}).get("warnings", []):
        print(f"AssemblyAI warning: {warning.get('message', warning)}", file=sys.stderr)
    if result.get("language_code") != "en_uk":
        raise RuntimeError("AssemblyAI did not confirm British English localisation "
                           f"for transcript {transcript_id}")
    text = result.get("text")
    if not isinstance(text, str):
        raise RuntimeError(f"Transcript {transcript_id} has no text")
    return {"transcript_id": transcript_id, "text": text}


def write_json(destination, result):
    # Replace only after the complete result has been written successfully.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                         dir=destination.parent, delete=False) as output:
            temporary = output.name
            json.dump(result, output, ensure_ascii=False)
            output.write("\n")
        os.replace(temporary, destination)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


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
        raise RuntimeError("Audio and output paths must differ")
    if not args.output.parent.is_dir():
        raise RuntimeError(f"Output directory does not exist: {args.output.parent}")
    write_json(args.output, transcribe(api_key, args.audio))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Transcription interrupted", file=sys.stderr)
        sys.exit(130)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
