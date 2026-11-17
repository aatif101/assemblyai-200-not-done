#!/usr/bin/env python3
"""Transcribe English audio with AssemblyAI's British spelling locale."""

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


BASE_URL = "https://api.assemblyai.com"


def request(api_key, method, path, data=None, content_type="application/json", size=None):
    headers = {"Authorization": api_key, "Content-Type": content_type}
    if size is not None:
        headers["Content-Length"] = str(size)
    req = Request(BASE_URL + path, data=data, headers=headers, method=method)
    try:
        with urlopen(req, timeout=120) as response:
            result = json.load(response)
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:2000]
        raise RuntimeError(f"AssemblyAI {method} {path}: HTTP {exc.code}: {detail}") from exc
    except (URLError, TimeoutError) as exc:
        raise RuntimeError(f"AssemblyAI {method} {path}: {exc}") from exc
    if not isinstance(result, dict):
        raise RuntimeError("AssemblyAI returned an invalid JSON response")
    return result


def transcribe(api_key, audio):
    with audio.open("rb") as source:
        size = os.fstat(source.fileno()).st_size
        if not size:
            raise ValueError("Audio file is empty")
        chunks = iter(lambda: source.read(1024 * 1024), b"")
        upload = request(api_key, "POST", "/v2/upload", chunks,
                         "application/octet-stream", size)
    upload_url = upload.get("upload_url")
    if not isinstance(upload_url, str) or not upload_url:
        raise RuntimeError("AssemblyAI upload response has no upload_url")

    config = {
        "audio_url": upload_url,
        "speech_models": ["universal-3-5-pro"],
        # Pin the locale directly: these memos are known to be English.
        # This controls spelling throughout, without a word replacement list.
        "language_code": "en_uk",
    }
    transcript = request(api_key, "POST", "/v2/transcript",
                         json.dumps(config).encode("utf-8"))
    transcript_id = transcript.get("id")
    if not isinstance(transcript_id, str) or not transcript_id:
        raise RuntimeError("AssemblyAI submission response has no transcript id")
    print(f"AssemblyAI transcript: {transcript_id}", file=sys.stderr)
    deadline = time.monotonic() + 3600
    while True:
        status = transcript.get("status")
        if status == "completed":
            text = transcript.get("text")
            if not isinstance(text, str):
                raise RuntimeError("Completed transcript has no text string")
            for warning in (transcript.get("metadata") or {}).get("warnings", []):
                print(f"AssemblyAI warning: {warning.get('message', warning)}", file=sys.stderr)
            return {"transcript_id": transcript_id, "text": text}
        if status == "error":
            raise RuntimeError(f"Transcription {transcript_id} failed: {transcript.get('error')}")
        if status not in ("queued", "processing"):
            raise RuntimeError(f"Unexpected transcript status: {status!r}")
        if time.monotonic() >= deadline:
            raise RuntimeError(f"Timed out waiting for transcript {transcript_id}")
        time.sleep(2)
        transcript = request(api_key, "GET", "/v2/transcript/" + quote(transcript_id, safe=""))


def write_output(path, result):
    # Replace only after writing a complete JSON document.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".transcript-", delete=False) as output:
            temporary = Path(output.name)
            json.dump(result, output, ensure_ascii=False)
            output.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    api_key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
    try:
        if not api_key:
            raise ValueError("ASSEMBLYAI_API_KEY is not set")
        if args.audio.resolve() == args.output.resolve():
            raise ValueError("Audio and output paths must differ")
        if not args.audio.is_file():
            raise ValueError(f"Audio file does not exist: {args.audio}")
        if not args.output.parent.is_dir():
            raise ValueError(f"Output directory does not exist: {args.output.parent}")
        write_output(args.output, transcribe(api_key, args.audio))
        return 0
    except Exception as exc:
        message = str(exc)
        if api_key:
            message = message.replace(api_key, "[redacted]")
        print(f"Error: {message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
