#!/usr/bin/env python3
"""Transcribe recorded English audio with AssemblyAI's British spelling locale."""

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
POLL_TIMEOUT_SECONDS = 3600


def request_json(api_key, method, path, *, payload=None, audio=None):
    headers = {"Authorization": api_key}
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")
    elif audio is not None:
        headers["Content-Type"] = "application/octet-stream"
        headers["Content-Length"] = str(os.fstat(audio.fileno()).st_size)
        data = audio
    request = urllib.request.Request(
        BASE_URL + path, data=data, headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"AssemblyAI {method} {path}: HTTP {exc.code}: {detail}") from exc
    if not isinstance(result, dict):
        raise RuntimeError("AssemblyAI returned an unexpected response")
    return result


def transcribe(api_key, audio_path):
    with audio_path.open("rb") as audio:
        upload = request_json(api_key, "POST", "/upload", audio=audio)
    upload_url = upload.get("upload_url")
    if not isinstance(upload_url, str) or not upload_url:
        raise RuntimeError("AssemblyAI upload response has no upload_url")

    # For known English audio, request the regional locale directly. A prompt
    # describing desired spelling does not control transcription formatting.
    transcript = request_json(
        api_key,
        "POST",
        "/transcript",
        payload={
            "audio_url": upload_url,
            "speech_models": ["universal-3-5-pro"],
            "language_code": "en_uk",
            "language_detection": False,
            "punctuate": True,
            "format_text": True,
        },
    )
    transcript_id = transcript.get("id")
    if not isinstance(transcript_id, str) or not transcript_id:
        raise RuntimeError("AssemblyAI transcript response has no id")

    deadline = time.monotonic() + POLL_TIMEOUT_SECONDS
    while True:
        status = transcript.get("status")
        if status == "completed":
            text = transcript.get("text")
            if not isinstance(text, str):
                raise RuntimeError(f"Transcript {transcript_id} has no text")
            return {"transcript_id": transcript_id, "text": text}
        if status == "error":
            raise RuntimeError(
                f"Transcript {transcript_id} failed: {transcript.get('error', 'unknown error')}"
            )
        if status not in ("queued", "processing"):
            raise RuntimeError(f"Transcript {transcript_id}: unexpected status {status!r}")
        if time.monotonic() >= deadline:
            raise RuntimeError(f"Timed out waiting for transcript {transcript_id}")
        time.sleep(3)
        transcript = request_json(
            api_key, "GET", "/transcript/" + urllib.parse.quote(transcript_id, safe="")
        )


def write_output(path, result):
    # Replace the destination only after a complete, successful transcription.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
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
        raise RuntimeError("Set the ASSEMBLYAI_API_KEY environment variable")
    if args.audio.resolve() == args.output.resolve():
        raise RuntimeError("Audio and output paths must be different")
    if not args.audio.is_file() or args.audio.stat().st_size == 0:
        raise RuntimeError(f"Audio file is missing or empty: {args.audio}")
    if not args.output.parent.is_dir() or args.output.is_dir():
        raise RuntimeError(f"Invalid output path: {args.output}")
    write_output(args.output, transcribe(api_key, args.audio))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Transcription interrupted", file=sys.stderr)
        sys.exit(130)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
