#!/usr/bin/env python3
"""Transcribe local audio with AssemblyAI using British English spelling."""

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import urllib.error
import urllib.request


BASE_URL = "https://api.assemblyai.com/v2"


def request(key, path, data=None, content_type="application/json", size=None):
    headers = {"Authorization": key, "Content-Type": content_type}
    if size is not None:
        headers["Content-Length"] = str(size)
    req = urllib.request.Request(BASE_URL + path, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        # Do not print headers or the API key when reporting a failed request.
        raise RuntimeError(f"AssemblyAI request failed (HTTP {exc.code})") from None
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Cannot reach AssemblyAI: {exc.reason}") from None


def transcribe(key, audio):
    with audio.open("rb") as source:
        size = os.fstat(source.fileno()).st_size
        if not size:
            raise ValueError("Audio file is empty")
        uploaded = request(key, "/upload", source, "application/octet-stream", size)
    submitted = request(key, "/transcript", json.dumps({
        "audio_url": uploaded["upload_url"],
        "language_code": "en_uk",
        "speech_models": ["universal-2"],
        "punctuate": True,
        "format_text": True,
    }).encode("utf-8"))
    transcript_id = submitted["id"]
    print(f"AssemblyAI transcript: {transcript_id}", file=sys.stderr)
    deadline = time.monotonic() + 6 * 60 * 60
    result = submitted
    while True:
        status = result.get("status")
        if status == "completed":
            text = result.get("text")
            if not isinstance(text, str):
                raise RuntimeError("AssemblyAI completed without transcript text")
            return {"transcript_id": transcript_id, "text": text}
        if status == "error":
            raise RuntimeError(f"AssemblyAI transcription failed: {result.get('error', 'unknown error')}")
        if status not in ("queued", "processing"):
            raise RuntimeError(f"Unexpected AssemblyAI status: {status!r}")
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Timed out waiting for transcript {transcript_id}")
        time.sleep(3)
        result = request(key, f"/transcript/{transcript_id}")


def write_output(output, result):
    # Replace only after a complete JSON file has been written successfully.
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
        key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
        if not key:
            raise ValueError("ASSEMBLYAI_API_KEY must be set")
        if not args.audio.is_file():
            raise ValueError(f"Audio file does not exist: {args.audio}")
        if args.audio.resolve() == args.output.resolve():
            raise ValueError("Audio and output must be different files")
        if not args.output.parent.is_dir():
            raise ValueError(f"Output directory does not exist: {args.output.parent}")
        write_output(args.output, transcribe(key, args.audio))
        return 0
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Transcription interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
