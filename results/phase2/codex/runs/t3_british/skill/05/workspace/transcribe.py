#!/usr/bin/env python3
"""Transcribe recorded English audio with AssemblyAI's British localization."""

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


BASE_URL = "https://api.assemblyai.com/v2"


def request_json(api_key, method, path, data=None, content_type=None, size=None):
    headers = {"Authorization": api_key}
    if content_type:
        headers["Content-Type"] = content_type
    if size is not None:
        headers["Content-Length"] = str(size)
    request = Request(BASE_URL + path, data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=120) as response:
            result = json.load(response)
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        # Never print the authentication header, even in diagnostic output.
        raise RuntimeError(f"AssemblyAI HTTP {exc.code}: {detail}") from exc
    if not isinstance(result, dict):
        raise RuntimeError("AssemblyAI returned an invalid JSON response")
    return result


def transcribe(api_key, audio):
    with audio.open("rb") as stream:
        upload = request_json(api_key, "POST", "/upload", stream,
                              "application/octet-stream", os.fstat(stream.fileno()).st_size)
    if not isinstance(upload.get("upload_url"), str) or not upload["upload_url"]:
        raise RuntimeError("AssemblyAI did not return an upload URL")

    config = {
        "audio_url": upload["upload_url"],
        "speech_models": ["universal-3-5-pro", "universal-2"],
        "language_detection": True,
        "language_detection_options": {
            "expected_languages": ["en"],
            "fallback_language": "en",
            "localization": ["en_uk"],
        },
    }
    result = request_json(api_key, "POST", "/transcript",
                          json.dumps(config).encode("utf-8"), "application/json")
    transcript_id = result.get("id")
    if not isinstance(transcript_id, str) or not transcript_id:
        raise RuntimeError("AssemblyAI did not return a transcript ID")
    deadline = time.monotonic() + 7200
    while True:
        status = result.get("status")
        if status == "completed":
            if not isinstance(result.get("text"), str):
                raise RuntimeError("Completed transcript has no text")
            for warning in (result.get("metadata") or {}).get("warnings", []):
                print(f"AssemblyAI warning: {warning.get('message', warning)}", file=sys.stderr)
            return {"transcript_id": transcript_id, "text": result["text"]}
        if status == "error":
            raise RuntimeError(f"Transcript {transcript_id} failed: {result.get('error', 'unknown error')}")
        if status not in ("queued", "processing"):
            raise RuntimeError(f"Unexpected transcript status: {status!r}")
        if time.monotonic() >= deadline:
            raise RuntimeError(f"Timed out waiting for transcript {transcript_id}")
        time.sleep(3)
        result = request_json(api_key, "GET", "/transcript/" + quote(transcript_id, safe=""))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    temporary = None
    try:
        api_key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
        if not api_key:
            raise ValueError("ASSEMBLYAI_API_KEY must be set")
        if not args.audio.is_file():
            raise ValueError(f"Audio file does not exist: {args.audio}")
        if args.audio.resolve() == args.output.resolve():
            raise ValueError("Output must be different from the audio file")
        # Check the output location before submitting a billable transcription.
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                         dir=args.output.absolute().parent,
                                         prefix=".transcript-", delete=False) as out:
            temporary = Path(out.name)
            result = transcribe(api_key, args.audio)
            json.dump(result, out, ensure_ascii=False)
            out.write("\n")
        os.replace(temporary, args.output)
        return 0
    except (OSError, ValueError, RuntimeError, URLError) as exc:
        message = str(exc)
        key = os.environ.get("ASSEMBLYAI_API_KEY")
        if key:
            message = message.replace(key, "[REDACTED]")
        print(f"Error: {message}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Transcription interrupted", file=sys.stderr)
        return 130
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
