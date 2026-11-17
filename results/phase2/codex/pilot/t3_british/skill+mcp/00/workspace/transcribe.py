#!/usr/bin/env python3
"""Transcribe local recordings with AssemblyAI's British English spelling."""

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


def request(api_key, method, endpoint, *, payload=None, audio=None):
    headers = {"Authorization": api_key}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    elif audio is not None:
        data = audio
        headers["Content-Type"] = "application/octet-stream"
        headers["Content-Length"] = str(os.fstat(audio.fileno()).st_size)
    req = Request(BASE_URL + endpoint, data=data, headers=headers, method=method)
    try:
        with urlopen(req, timeout=120) as response:
            result = json.load(response)
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"AssemblyAI {method} {endpoint}: HTTP {exc.code}: {detail}") from exc
    if not isinstance(result, dict):
        raise RuntimeError("AssemblyAI returned an invalid response")
    return result


def transcribe(api_key, audio_path):
    with audio_path.open("rb") as audio:
        if os.fstat(audio.fileno()).st_size == 0:
            raise ValueError("Audio file is empty")
        uploaded = request(api_key, "POST", "/upload", audio=audio)
    upload_url = uploaded.get("upload_url")
    if not isinstance(upload_url, str) or not upload_url:
        raise RuntimeError("AssemblyAI did not return an upload URL")
    transcript = request(api_key, "POST", "/transcript", payload={
        "audio_url": upload_url,
        "speech_models": ["universal-3-5-pro"],
        # Known English audio: set the locale directly, including short memos.
        # https://assemblyai.com/docs/pre-recorded-audio/language-detection#choose-a-locale
        "language_code": "en_uk",
    })
    transcript_id = transcript.get("id")
    if not isinstance(transcript_id, str) or not transcript_id:
        raise RuntimeError("AssemblyAI did not return a transcript ID")
    deadline = time.monotonic() + 3600
    while True:
        status = transcript.get("status")
        if status == "completed":
            text = transcript.get("text")
            if not isinstance(text, str):
                raise RuntimeError("Completed transcript has no text")
            return {"transcript_id": transcript_id, "text": text}
        if status == "error":
            raise RuntimeError(f"Transcription {transcript_id} failed: {transcript.get('error')}")
        if status not in ("queued", "processing"):
            raise RuntimeError(f"Unexpected transcript status: {status!r}")
        if time.monotonic() >= deadline:
            raise RuntimeError(f"Timed out waiting for transcript {transcript_id}")
        time.sleep(3)
        transcript = request(api_key, "GET", "/transcript/" + quote(transcript_id, safe=""))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    temporary = None
    try:
        api_key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
        if not api_key:
            raise ValueError("ASSEMBLYAI_API_KEY is not set")
        if args.audio.resolve() == args.output.resolve():
            raise ValueError("Audio and output paths must be different")
        if not args.output.parent.is_dir():
            raise ValueError("Output directory does not exist")
        result = transcribe(api_key, args.audio)
        # Only replace the destination once a complete JSON result is ready.
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                         dir=args.output.parent, delete=False) as stream:
            temporary = stream.name
            json.dump(result, stream, ensure_ascii=False)
            stream.write("\n")
        os.replace(temporary, args.output)
        temporary = None
        return 0
    except (OSError, ValueError, RuntimeError, URLError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Transcription interrupted", file=sys.stderr)
        return 130
    finally:
        if temporary is not None:
            os.unlink(temporary)


if __name__ == "__main__":
    sys.exit(main())
