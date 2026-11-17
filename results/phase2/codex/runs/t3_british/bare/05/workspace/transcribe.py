#!/usr/bin/env python3
"""Upload and transcribe English audio using AssemblyAI's UK spelling locale."""

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
POLL_TIMEOUT = 3600


def request(api_key, method, path, data=None, headers=None):
    req = urllib.request.Request(
        API_URL + path,
        data=data,
        method=method,
        headers={"Authorization": api_key, **(headers or {})},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        # Report the service's error without printing request headers or the key.
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"AssemblyAI HTTP {exc.code}: {detail}") from exc
    if not isinstance(result, dict):
        raise RuntimeError("AssemblyAI returned an invalid response")
    return result


def transcribe(audio, api_key):
    with audio.open("rb") as source:
        size = os.fstat(source.fileno()).st_size
        if not size:
            raise ValueError("Audio file is empty")
        upload = request(
            api_key, "POST", "/upload", source,
            {"Content-Type": "application/octet-stream", "Content-Length": str(size)},
        )
    payload = {
        "audio_url": upload["upload_url"],
        # This locale controls British spellings, including colour and organise.
        # Universal-2 supports this regional English locale.
        "language_code": "en_uk",
        "speech_models": ["universal-2"],
        "punctuate": True,
        "format_text": True,
    }
    job = request(
        api_key, "POST", "/transcript", json.dumps(payload).encode("utf-8"),
        {"Content-Type": "application/json"},
    )
    transcript_id = job.get("id")
    if not isinstance(transcript_id, str) or not transcript_id:
        raise RuntimeError("AssemblyAI did not return a transcript ID")
    deadline = time.monotonic() + POLL_TIMEOUT
    while True:
        status = job.get("status")
        if status == "completed":
            if not isinstance(job.get("text"), str):
                raise RuntimeError("Completed transcript has no text")
            return {"transcript_id": transcript_id, "text": job["text"]}
        if status == "error":
            raise RuntimeError(f"Transcription {transcript_id} failed: {job.get('error', 'unknown error')}")
        if status not in ("queued", "processing"):
            raise RuntimeError(f"Unexpected transcript status: {status!r}")
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Timed out waiting for transcript {transcript_id}")
        time.sleep(2)
        job = request(api_key, "GET", f"/transcript/{transcript_id}")


def write_result(output, result):
    # Replace the destination only after a complete successful transcription.
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=output.parent,
            prefix=f".{output.name}.", suffix=".tmp", delete=False,
        ) as stream:
            temp_path = Path(stream.name)
            json.dump(result, stream, ensure_ascii=False)
            stream.write("\n")
        os.replace(temp_path, output)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    try:
        api_key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
        if not api_key:
            raise ValueError("ASSEMBLYAI_API_KEY must be set")
        if not args.audio.is_file():
            raise ValueError(f"Audio file does not exist: {args.audio}")
        if args.audio.resolve() == args.output.resolve():
            raise ValueError("Output path must differ from the audio path")
        if not args.output.parent.is_dir():
            raise ValueError(f"Output directory does not exist: {args.output.parent}")
        write_result(args.output, transcribe(args.audio, api_key))
        return 0
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Transcription interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
