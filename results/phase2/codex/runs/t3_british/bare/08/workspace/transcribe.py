#!/usr/bin/env python3
"""Transcribe local audio through AssemblyAI with British English spelling."""

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


API = "https://api.assemblyai.com/v2"
JOB_TIMEOUT_SECONDS = 3600


def request_json(key, method, path, data=None, headers=None):
    request = urllib.request.Request(
        API + path,
        data=data,
        method=method,
        headers={"authorization": key, **(headers or {})},
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"AssemblyAI {method} {path}: HTTP {exc.code}: {detail}") from None


def transcribe(key, audio):
    with audio.open("rb") as stream:
        upload = request_json(
            key, "POST", "/upload", stream,
            {"content-type": "application/octet-stream",
             "content-length": str(os.fstat(stream.fileno()).st_size)},
        )

    # The locale controls spelling across the transcript without a word list.
    config = {
        "audio_url": upload["upload_url"],
        "language_code": "en_uk",
        "speech_models": ["universal-3-pro"],
        "punctuate": True,
        "format_text": True,
    }
    job = request_json(key, "POST", "/transcript", json.dumps(config).encode(),
                       {"content-type": "application/json"})
    transcript_id = job.get("id")
    if not isinstance(transcript_id, str) or not transcript_id:
        raise RuntimeError("AssemblyAI did not return a transcript id")

    deadline = time.monotonic() + JOB_TIMEOUT_SECONDS
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
        if time.monotonic() >= deadline:
            raise RuntimeError(f"Timed out waiting for transcript {transcript_id}")
        time.sleep(3)
        job = request_json(key, "GET", "/transcript/" + urllib.parse.quote(transcript_id, safe=""))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    temporary = None
    try:
        key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
        if not key:
            raise ValueError("ASSEMBLYAI_API_KEY must be set")
        if not args.audio.is_file():
            raise ValueError(f"Audio file does not exist: {args.audio}")
        if args.audio.stat().st_size == 0:
            raise ValueError("Audio file is empty")
        if args.audio.resolve() == args.output.resolve():
            raise ValueError("Output must be different from the audio file")
        if not args.output.parent.is_dir():
            raise ValueError(f"Output directory does not exist: {args.output.parent}")

        result = transcribe(key, args.audio)
        # Replace the destination only after receiving a successful transcript.
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=args.output.parent,
                                         prefix=".transcript-", delete=False) as output:
            temporary = output.name
            json.dump(result, output, ensure_ascii=False)
            output.write("\n")
        os.replace(temporary, args.output)
        temporary = None
        return 0
    except (Exception, KeyboardInterrupt) as exc:
        message = str(exc) or "Interrupted"
        key = os.environ.get("ASSEMBLYAI_API_KEY")
        if key:
            message = message.replace(key, "[redacted]")
        print(f"Error: {message}", file=sys.stderr)
        return 1
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
