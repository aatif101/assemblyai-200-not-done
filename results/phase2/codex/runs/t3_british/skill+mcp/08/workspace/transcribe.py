#!/usr/bin/env python3
"""Transcribe recorded audio with AssemblyAI's British English spelling."""

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


def request_json(api_key, method, path, data=None, content_type=None, length=None):
    headers = {"Authorization": api_key, "Accept": "application/json"}
    if content_type:
        headers["Content-Type"] = content_type
    if length is not None:
        headers["Content-Length"] = str(length)
    request = Request(BASE_URL + path, data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=120) as response:
            result = json.load(response)
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:2000]
        raise RuntimeError(f"AssemblyAI HTTP {exc.code}: {detail}") from None
    if not isinstance(result, dict):
        raise RuntimeError("AssemblyAI returned an unexpected JSON response")
    return result


def transcribe(api_key, audio):
    with audio.open("rb") as stream:
        uploaded = request_json(
            api_key, "POST", "/upload", stream, "application/octet-stream",
            os.fstat(stream.fileno()).st_size,
        )
    upload_url = uploaded.get("upload_url")
    if not isinstance(upload_url, str) or not upload_url:
        raise RuntimeError("AssemblyAI did not return an upload URL")

    # The language is known. en_uk selects British spelling directly, without
    # relying on language detection (which is less reliable on short memos).
    payload = {
        "audio_url": upload_url,
        "speech_models": ["universal-3-5-pro"],
        "language_code": "en_uk",
    }
    transcript = request_json(
        api_key, "POST", "/transcript", json.dumps(payload).encode("utf-8"),
        "application/json",
    )
    transcript_id = transcript.get("id")
    if not isinstance(transcript_id, str) or not transcript_id:
        raise RuntimeError("AssemblyAI did not return a transcript ID")

    deadline = time.monotonic() + 1800
    while True:
        status = transcript.get("status")
        if status == "completed":
            for warning in (transcript.get("metadata") or {}).get("warnings", []):
                print(f"AssemblyAI warning: {warning.get('message', warning)}", file=sys.stderr)
            text = transcript.get("text")
            if not isinstance(text, str):
                raise RuntimeError("AssemblyAI completed without transcript text")
            return {"transcript_id": transcript_id, "text": text}
        if status == "error":
            raise RuntimeError(f"Transcript {transcript_id} failed: {transcript.get('error')}")
        if status not in ("queued", "processing"):
            raise RuntimeError(f"Unexpected transcript status: {status!r}")
        if time.monotonic() >= deadline:
            raise RuntimeError(f"Timed out waiting for transcript {transcript_id}")
        time.sleep(3)
        transcript = request_json(api_key, "GET", "/transcript/" + quote(transcript_id, safe=""))


def write_result(output, result):
    # Replace the destination only after a complete JSON document is written.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=output.parent, delete=False,
        ) as stream:
            temporary = stream.name
            json.dump(result, stream, ensure_ascii=False)
            stream.write("\n")
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
        api_key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
        if not api_key:
            raise ValueError("ASSEMBLYAI_API_KEY must be set")
        if args.audio.resolve() == args.output.resolve():
            raise ValueError("Audio input and JSON output must be different files")
        if not args.output.parent.is_dir():
            raise ValueError(f"Output directory does not exist: {args.output.parent}")
        write_result(args.output, transcribe(api_key, args.audio))
    except (OSError, URLError, ValueError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
