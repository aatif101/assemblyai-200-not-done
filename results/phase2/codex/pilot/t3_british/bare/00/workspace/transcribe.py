#!/usr/bin/env python3
"""Transcribe a local recording with AssemblyAI using British English spelling."""

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import urllib.error
import urllib.request


API_URL = "https://api.assemblyai.com/v2"
POLL_INTERVAL = 3
POLL_TIMEOUT = 1800


def request_json(method, path, api_key, data=None, content_type=None, length=None):
    headers = {"authorization": api_key}
    if content_type:
        headers["content-type"] = content_type
    if length is not None:
        headers["content-length"] = str(length)
    request = urllib.request.Request(
        API_URL + path, data=data, headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        # Never include the API key in diagnostics, even if a server echoes it.
        raise RuntimeError(
            f"AssemblyAI {method} {path}: HTTP {exc.code}: {detail}"
        ) from None
    if not isinstance(result, dict):
        raise RuntimeError("AssemblyAI returned an invalid JSON response")
    return result


def transcribe(audio_path, api_key):
    # Stream the file rather than loading an entire voice memo into memory.
    with audio_path.open("rb") as audio:
        uploaded = request_json(
            "POST", "/upload", api_key, data=audio,
            content_type="application/octet-stream",
            length=os.fstat(audio.fileno()).st_size,
        )
    audio_url = uploaded.get("upload_url")
    if not isinstance(audio_url, str) or not audio_url:
        raise RuntimeError("AssemblyAI upload response has no upload_url")

    # Select a model that supports regional English spelling explicitly.
    # Keep the API's text verbatim so it matches the transcript identified below.
    payload = {
        "audio_url": audio_url,
        "speech_models": ["universal-2"],
        "language_code": "en_uk",
        "punctuate": True,
        "format_text": True,
    }
    transcript = request_json(
        "POST", "/transcript", api_key,
        data=json.dumps(payload).encode("utf-8"),
        content_type="application/json",
    )
    transcript_id = transcript.get("id")
    if not isinstance(transcript_id, str) or not transcript_id:
        raise RuntimeError("AssemblyAI response has no transcript id")
    print(f"AssemblyAI transcript: {transcript_id}", file=sys.stderr)

    deadline = time.monotonic() + POLL_TIMEOUT
    while True:
        status = transcript.get("status")
        if status == "completed":
            text = transcript.get("text")
            if not isinstance(text, str):
                raise RuntimeError(f"Transcript {transcript_id} has no text")
            return {"transcript_id": transcript_id, "text": text}
        if status == "error":
            raise RuntimeError(
                f"Transcript {transcript_id} failed: {transcript.get('error')}"
            )
        if status not in ("queued", "processing"):
            raise RuntimeError(f"Unexpected transcription status: {status!r}")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError(f"Timed out waiting for transcript {transcript_id}")
        time.sleep(min(POLL_INTERVAL, remaining))
        transcript = request_json("GET", f"/transcript/{transcript_id}", api_key)


def write_result(output_path, result):
    # Replace the destination only once the complete JSON has been written.
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=output_path.parent,
            prefix=".transcript-", suffix=".tmp", delete=False,
        ) as output:
            temporary_path = Path(output.name)
            json.dump(result, output, ensure_ascii=False)
            output.write("\n")
        os.replace(temporary_path, output_path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path, help="Local audio file")
    parser.add_argument("output", type=Path, help="Destination JSON file")
    args = parser.parse_args()
    api_key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
    try:
        if not api_key:
            raise RuntimeError("Set ASSEMBLYAI_API_KEY in the environment")
        if not args.audio.is_file() or args.audio.stat().st_size == 0:
            raise RuntimeError(f"Audio file is missing or empty: {args.audio}")
        if args.audio.resolve() == args.output.resolve():
            raise RuntimeError("Audio and output paths must be different")
        if not args.output.parent.is_dir() or args.output.is_dir():
            raise RuntimeError(f"Invalid output path: {args.output}")
        result = transcribe(args.audio, api_key)
        write_result(args.output, result)
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


if __name__ == "__main__":
    sys.exit(main())
