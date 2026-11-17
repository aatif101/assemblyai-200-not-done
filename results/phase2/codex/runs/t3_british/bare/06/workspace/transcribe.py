#!/usr/bin/env python3
"""Transcribe a local audio file with AssemblyAI using British spelling."""

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
JOB_TIMEOUT = 3600


def request_json(key, method, path, data=None, headers=None, timeout=120):
    request = urllib.request.Request(
        API_URL + path,
        data=data,
        method=method,
        headers={"Authorization": key, **(headers or {})},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        message = exc.reason
        try:
            details = json.load(exc)
            message = details.get("error", message)
        except (ValueError, AttributeError):
            pass
        raise RuntimeError(f"AssemblyAI HTTP {exc.code}: {message}") from None
    if not isinstance(result, dict):
        raise RuntimeError("AssemblyAI returned an invalid JSON response")
    return result


def transcribe(key, audio):
    with audio.open("rb") as source:
        upload = request_json(
            key, "POST", "/upload", source,
            {"Content-Type": "application/octet-stream",
             "Content-Length": str(os.fstat(source.fileno()).st_size)},
        )
    if not upload.get("upload_url"):
        raise RuntimeError("AssemblyAI upload response has no upload_url")

    # This locale controls output spelling as well as the transcription language.
    # Universal-2 supports the British English locale directly.
    config = {
        "audio_url": upload["upload_url"],
        "speech_models": ["universal-2"],
        "language_code": "en_uk",
        "punctuate": True,
        "format_text": True,
    }
    job = request_json(
        key, "POST", "/transcript", json.dumps(config).encode("utf-8"),
        {"Content-Type": "application/json"},
    )
    transcript_id = job.get("id")
    if not isinstance(transcript_id, str) or not transcript_id:
        raise RuntimeError("AssemblyAI transcript response has no id")
    deadline = time.monotonic() + JOB_TIMEOUT
    while True:
        status = job.get("status")
        if status == "completed":
            if not isinstance(job.get("text"), str):
                raise RuntimeError(f"Transcript {transcript_id} completed without text")
            return {"transcript_id": transcript_id, "text": job["text"]}
        if status == "error":
            raise RuntimeError(f"Transcript {transcript_id} failed: {job.get('error')}")
        if status not in ("queued", "processing"):
            raise RuntimeError(f"Transcript {transcript_id}: unexpected status {status!r}")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError(f"Timed out waiting for transcript {transcript_id}")
        time.sleep(min(3, remaining))
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError(f"Timed out waiting for transcript {transcript_id}")
        job = request_json(
            key, "GET", f"/transcript/{transcript_id}", timeout=min(120, remaining)
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("ASSEMBLYAI_API_KEY is not set")
    if not args.audio.is_file() or args.audio.stat().st_size == 0:
        raise RuntimeError(f"Audio file is missing or empty: {args.audio}")
    if args.audio.resolve() == args.output.resolve():
        raise RuntimeError("Audio and output paths must differ")
    if not args.output.parent.is_dir() or args.output.is_dir():
        raise RuntimeError(f"Invalid output path: {args.output}")

    result = transcribe(key, args.audio)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=args.output.parent,
            prefix=".transcript-", suffix=".json", delete=False,
        ) as output:
            temporary = output.name
            json.dump(result, output, ensure_ascii=False)
            output.write("\n")
        os.replace(temporary, args.output)
    finally:
        if temporary is not None and os.path.exists(temporary):
            os.unlink(temporary)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        sys.exit(130)
