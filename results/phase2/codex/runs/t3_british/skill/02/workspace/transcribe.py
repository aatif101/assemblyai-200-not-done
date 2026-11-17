#!/usr/bin/env python3
"""Transcribe recorded English audio with AssemblyAI's British spelling."""

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


def request(api_key, method, path, data=None, headers=None):
    req = urllib.request.Request(
        API + path,
        data=data,
        method=method,
        headers={"Authorization": api_key, **(headers or {})},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"AssemblyAI {method} {path}: HTTP {exc.code}: {detail}") from None
    if not isinstance(result, dict):
        raise RuntimeError("AssemblyAI returned an invalid response")
    return result


def transcribe(api_key, audio):
    with audio.open("rb") as source:
        size = os.fstat(source.fileno()).st_size
        if not size:
            raise ValueError("Audio file is empty")
        upload = request(api_key, "POST", "/upload", source, {
            "Content-Type": "application/octet-stream",
            "Content-Length": str(size),
        })
    payload = {
        "audio_url": upload["upload_url"],
        "speech_models": ["universal-3-5-pro"],
        # This selects regional spelling; a contextual prompt cannot do so.
        "language_code": "en_uk",
    }
    result = request(api_key, "POST", "/transcript",
                     json.dumps(payload).encode("utf-8"),
                     {"Content-Type": "application/json"})
    transcript_id = result.get("id")
    if not isinstance(transcript_id, str) or not transcript_id:
        raise RuntimeError("AssemblyAI did not return a transcript ID")
    print(f"AssemblyAI transcript: {transcript_id}", file=sys.stderr)
    deadline = time.monotonic() + 3600
    while True:
        status = result.get("status")
        if status == "completed":
            if not isinstance(result.get("text"), str):
                raise RuntimeError("AssemblyAI completed without transcript text")
            return {"transcript_id": transcript_id, "text": result["text"]}
        if status == "error":
            raise RuntimeError(f"Transcription {transcript_id} failed: {result.get('error')}")
        if status not in ("queued", "processing"):
            raise RuntimeError(f"Unexpected transcription status: {status!r}")
        if time.monotonic() >= deadline:
            raise RuntimeError(f"Timed out waiting for transcript {transcript_id}")
        time.sleep(3)
        result = request(api_key, "GET", "/transcript/" + urllib.parse.quote(transcript_id, safe=""))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    temporary = None
    try:
        api_key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
        if not api_key:
            raise ValueError("Set ASSEMBLYAI_API_KEY in the environment")
        if args.audio.resolve() == args.output.resolve():
            raise ValueError("Output path must differ from the audio path")
        if not args.output.parent.is_dir():
            raise ValueError("Output directory does not exist")
        result = transcribe(api_key, args.audio)
        # Publish only a complete JSON result; preserve existing output on failure.
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                         dir=args.output.parent, delete=False) as target:
            temporary = target.name
            json.dump(result, target, ensure_ascii=False)
            target.write("\n")
        os.replace(temporary, args.output)
        temporary = None
        return 0
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        return 130
    finally:
        if temporary is not None:
            os.unlink(temporary)


if __name__ == "__main__":
    sys.exit(main())
