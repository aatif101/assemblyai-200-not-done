#!/usr/bin/env python3
"""Upload a recording to AssemblyAI and request British English spelling."""

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
POLL_TIMEOUT = 3600


def request(api_key, path, data=None, content_type="application/json", length=None):
    headers = {"Authorization": api_key, "Content-Type": content_type}
    if length is not None:
        headers["Content-Length"] = str(length)
    req = urllib.request.Request(API_URL + path, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        # Do not print request headers (which contain the API key).
        raise RuntimeError(f"AssemblyAI returned HTTP {exc.code}") from None
    if not isinstance(result, dict):
        raise RuntimeError("AssemblyAI returned an invalid JSON response")
    return result


def transcribe(api_key, audio):
    with audio.open("rb") as recording:
        upload = request(
            api_key, "/upload", recording, "application/octet-stream",
            os.fstat(recording.fileno()).st_size,
        )
    if not upload.get("upload_url"):
        raise RuntimeError("AssemblyAI did not return an upload URL")

    # en_uk controls spelling, independently of the speaker's accent.
    # Pin the model tested with this locale instead of relying on API defaults.
    payload = {
        "audio_url": upload["upload_url"],
        "language_code": "en_uk",
        "speech_models": ["universal-2"],
        "punctuate": True,
        "format_text": True,
    }
    transcript = request(api_key, "/transcript", json.dumps(payload).encode("utf-8"))
    transcript_id = transcript.get("id")
    if not isinstance(transcript_id, str) or not transcript_id:
        raise RuntimeError("AssemblyAI did not return a transcript ID")

    deadline = time.monotonic() + POLL_TIMEOUT
    while True:
        status = transcript.get("status")
        if status == "completed":
            text = transcript.get("text")
            if not isinstance(text, str):
                raise RuntimeError("Completed transcript has no text")
            return {"transcript_id": transcript_id, "text": text}
        if status == "error":
            raise RuntimeError(
                f"Transcription {transcript_id} failed: "
                f"{transcript.get('error') or 'unknown error'}"
            )
        if status not in ("queued", "processing"):
            raise RuntimeError(f"Unexpected transcript status: {status!r}")
        if time.monotonic() >= deadline:
            raise RuntimeError(f"Timed out waiting for transcript {transcript_id}")
        time.sleep(2)
        transcript = request(
            api_key, "/transcript/" + urllib.parse.quote(transcript_id, safe="")
        )


def write_result(output, result):
    # Replace the destination only once a complete JSON file is ready.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=output.parent,
            prefix=".transcript-", suffix=".json", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump(result, stream, ensure_ascii=False)
            stream.write("\n")
        os.replace(temporary, output)
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
            raise RuntimeError("Set ASSEMBLYAI_API_KEY in the environment")
        if not args.audio.is_file():
            raise RuntimeError(f"Audio file does not exist: {args.audio}")
        if args.audio.stat().st_size == 0:
            raise RuntimeError("Audio file is empty")
        if args.audio.resolve() == args.output.resolve():
            raise RuntimeError("Output must be a different file from the audio")
        if not args.output.parent.is_dir():
            raise RuntimeError(f"Output directory does not exist: {args.output.parent}")
        if args.output.is_dir():
            raise RuntimeError("Output path is a directory")
        write_result(args.output, transcribe(api_key, args.audio))
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Transcription interrupted", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
