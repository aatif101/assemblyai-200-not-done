#!/usr/bin/env python3
"""Transcribe a local recording with AssemblyAI's British English locale."""

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import urllib.error
import urllib.request


API = "https://api.assemblyai.com/v2"
JOB_TIMEOUT = 7200


def request(key, method, path, body=None, content_type="application/json"):
    req = urllib.request.Request(
        API + path,
        data=body,
        method=method,
        headers={"authorization": key, "content-type": content_type},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"AssemblyAI HTTP {exc.code}: {detail}") from None
    except urllib.error.URLError as exc:
        raise RuntimeError(f"AssemblyAI connection failed: {exc.reason}") from None


def transcribe(key, audio):
    # Stream the file rather than loading an entire recording into memory.
    with audio.open("rb") as stream:
        uploaded = request(key, "POST", "/upload", stream, "application/octet-stream")
    job = request(
        key,
        "POST",
        "/transcript",
        json.dumps({
            "audio_url": uploaded["upload_url"],
            "speech_models": ["universal-2"],
            "language_code": "en_uk",
            "punctuate": True,
            "format_text": True,
        }).encode("utf-8"),
    )
    transcript_id = job["id"]
    print(f"AssemblyAI transcript: {transcript_id}", file=sys.stderr)
    deadline = time.monotonic() + JOB_TIMEOUT
    while True:
        status = job.get("status")
        if status == "completed":
            if not isinstance(job.get("text"), str):
                raise RuntimeError("AssemblyAI completed without transcript text")
            return {"transcript_id": transcript_id, "text": job["text"]}
        if status == "error":
            raise RuntimeError(f"AssemblyAI transcription failed: {job.get('error', 'unknown error')}")
        if status not in ("queued", "processing"):
            raise RuntimeError(f"Unexpected AssemblyAI status: {status!r}")
        if time.monotonic() >= deadline:
            raise RuntimeError(f"Timed out waiting for transcript {transcript_id}")
        time.sleep(3)
        job = request(key, "GET", f"/transcript/{transcript_id}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("ASSEMBLYAI_API_KEY must be set")
    if not args.audio.is_file() or args.audio.stat().st_size == 0:
        raise RuntimeError(f"Audio file is missing or empty: {args.audio}")
    if args.audio.resolve() == args.output.resolve():
        raise RuntimeError("Output path must differ from the input audio path")
    if not args.output.parent.is_dir():
        raise RuntimeError(f"Output directory does not exist: {args.output.parent}")
    # Create the temporary output first to check writability before paid API work.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=args.output.parent,
            prefix=f".{args.output.name}.", suffix=".tmp", delete=False,
        ) as output:
            temporary = Path(output.name)
            result = transcribe(key, args.audio)
            json.dump(result, output, ensure_ascii=False)
            output.write("\n")
        os.replace(temporary, args.output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Transcription interrupted", file=sys.stderr)
        sys.exit(130)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
