#!/usr/bin/env python3
"""Transcribe a recording with AssemblyAI's British English locale."""

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


def request(api_key, method, path, data=None, content_type="application/json"):
    req = urllib.request.Request(
        BASE_URL + path,
        data=data,
        method=method,
        headers={"Authorization": api_key, "Content-Type": content_type},
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
        # The upload endpoint expects raw audio bytes, not JSON or multipart.
        upload = request(api_key, "POST", "/upload", audio.read(), "application/octet-stream")
    audio_url = upload.get("upload_url")
    if not isinstance(audio_url, str) or not audio_url:
        raise RuntimeError("AssemblyAI upload returned no upload_url")

    job = request(api_key, "POST", "/transcript", json.dumps({
        "audio_url": audio_url,
        "speech_models": ["universal-3-5-pro"],
        # Explicit locale applies British spelling even to short voice memos.
        # No language detection or spelling instructions in a context prompt.
        "language_code": "en_uk",
        "language_detection": False,
    }).encode("utf-8"))
    transcript_id = job.get("id")
    if not isinstance(transcript_id, str) or not transcript_id:
        raise RuntimeError("AssemblyAI returned no transcript id")
    print(f"AssemblyAI transcript: {transcript_id}", file=sys.stderr)

    deadline = time.monotonic() + 3600
    while True:
        status = job.get("status")
        if status == "completed":
            if not isinstance(job.get("text"), str):
                raise RuntimeError("Completed transcript has no text")
            for warning in (job.get("metadata") or {}).get("warnings", []):
                print(f"AssemblyAI warning: {warning.get('message', warning)}", file=sys.stderr)
            return {"transcript_id": transcript_id, "text": job["text"]}
        if status == "error":
            raise RuntimeError(f"Transcription {transcript_id} failed: {job.get('error', 'unknown error')}")
        if status not in ("queued", "processing"):
            raise RuntimeError(f"Unexpected transcript status: {status!r}")
        if time.monotonic() >= deadline:
            raise RuntimeError(f"Timed out waiting for transcript {transcript_id}")
        time.sleep(3)
        job = request(api_key, "GET", "/transcript/" + urllib.parse.quote(transcript_id, safe=""))


def write_result(output_path, result):
    # Replace atomically so a failed run cannot leave partial JSON.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=output_path.parent,
                                         prefix=".transcript-", suffix=".json", delete=False) as out:
            temporary = Path(out.name)
            json.dump(result, out, ensure_ascii=False)
            out.write("\n")
        os.replace(temporary, output_path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    try:
        api_key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
        if not api_key:
            raise ValueError("ASSEMBLYAI_API_KEY must be set")
        if args.audio.resolve() == args.output.resolve():
            raise ValueError("Audio and output paths must be different")
        if not args.audio.is_file():
            raise ValueError(f"Audio file does not exist: {args.audio}")
        if not args.output.parent.is_dir():
            raise ValueError(f"Output directory does not exist: {args.output.parent}")
        write_result(args.output, transcribe(api_key, args.audio))
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
