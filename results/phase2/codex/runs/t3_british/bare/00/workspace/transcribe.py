#!/usr/bin/env python3
"""Upload a recording to AssemblyAI and save its British English transcript."""

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


API_URL = "https://api.assemblyai.com/v2"


def request_json(key, method, path, data=None, headers=None):
    request = urllib.request.Request(
        API_URL + path,
        data=data,
        method=method,
        headers={"Authorization": key, **(headers or {})},
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read(4096).decode("utf-8", errors="replace")
        raise RuntimeError(f"AssemblyAI HTTP {exc.code}: {detail}") from exc


def transcribe(audio, key):
    with audio.open("rb") as recording:
        size = os.fstat(recording.fileno()).st_size
        if size == 0:
            raise ValueError("The audio file is empty")
        upload = request_json(
            key, "POST", "/upload", recording,
            {"Content-Type": "application/octet-stream", "Content-Length": str(size)},
        )

    # Use the British English locale explicitly: the default is US English.
    # Universal-2 supports this locale, including regional spelling.
    payload = {
        "audio_url": upload["upload_url"],
        "speech_models": ["universal-2"],
        "language_code": "en_uk",
        "punctuate": True,
        "format_text": True,
    }
    transcript = request_json(
        key, "POST", "/transcript", json.dumps(payload).encode("utf-8"),
        {"Content-Type": "application/json"},
    )
    transcript_id = transcript["id"]
    if not isinstance(transcript_id, str) or not transcript_id:
        raise RuntimeError("AssemblyAI returned an invalid transcript id")
    deadline = time.monotonic() + 3600
    while True:
        status = transcript.get("status")
        if status == "completed":
            text = transcript.get("text")
            if not isinstance(text, str):
                raise RuntimeError("AssemblyAI completed without transcript text")
            return {"transcript_id": transcript_id, "text": text}
        if status == "error":
            raise RuntimeError(
                f"AssemblyAI transcript {transcript_id} failed: "
                f"{transcript.get('error', 'unknown error')}"
            )
        if status not in ("queued", "processing"):
            raise RuntimeError(f"Unexpected AssemblyAI status: {status!r}")
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Timed out waiting for transcript {transcript_id}")
        time.sleep(3)
        transcript = request_json(
            key, "GET", "/transcript/" + urllib.parse.quote(transcript_id, safe="")
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
    if not key:
        raise ValueError("ASSEMBLYAI_API_KEY must be set")
    if args.audio.resolve() == args.output.resolve():
        raise ValueError("The output path must differ from the audio path")
    if not args.audio.is_file():
        raise ValueError(f"Audio file not found: {args.audio}")
    if not args.output.parent.is_dir():
        raise ValueError(f"Output directory does not exist: {args.output.parent}")
    result = transcribe(args.audio, key)
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
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Transcription interrupted", file=sys.stderr)
        sys.exit(130)
    except Exception as exc:
        # Never include the API key in diagnostics, even if an upstream error echoes it.
        message = str(exc)
        secret = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
        if secret:
            message = message.replace(secret, "[redacted]")
        print(f"Transcription failed: {message}", file=sys.stderr)
        sys.exit(1)
