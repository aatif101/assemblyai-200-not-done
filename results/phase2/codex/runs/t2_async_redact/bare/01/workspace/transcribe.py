#!/usr/bin/env python3
"""Upload a call and save only AssemblyAI's server-redacted transcript."""

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import time

import httpx


POLICIES = ["person_name", "phone_number", "us_social_security_number"]
API = "https://api.assemblyai.com/v2"


def request_json(client, method, path, **kwargs):
    response = client.request(method, API + path, **kwargs)
    if response.is_error:
        # Response bodies can contain customer data. Never log them.
        raise RuntimeError(f"AssemblyAI {method} {path}: HTTP {response.status_code}")
    return response.json()


def transcribe(audio, api_key):
    with httpx.Client(headers={"authorization": api_key}, timeout=120.0) as client:
        with audio.open("rb") as source:
            uploaded = request_json(
                client, "POST", "/upload",
                content=iter(lambda: source.read(1024 * 1024), b""),
                headers={"content-type": "application/octet-stream"},
            )
        result = request_json(client, "POST", "/transcript", json={
            "audio_url": uploaded["upload_url"],
            "speech_models": ["universal-2"],
            "language_code": "en_us",
            "speaker_labels": True,
            "speakers_expected": 2,
            "redact_pii": True,
            "redact_pii_policies": POLICIES,
            "redact_pii_sub": "entity_name",
            # Apply this at creation, so unredacted copies are not retained.
            "redact_pii_return_unredacted": False,
        })
        transcript_id = result["id"]
        print(f"AssemblyAI transcript: {transcript_id}", file=sys.stderr)
        deadline = time.monotonic() + 6 * 60 * 60
        while result.get("status") != "completed":
            if result.get("status") == "error":
                raise RuntimeError(f"AssemblyAI transcription failed (id {transcript_id})")
            if result.get("status") not in ("queued", "processing"):
                raise RuntimeError("AssemblyAI returned an unexpected transcript status")
            if time.monotonic() >= deadline:
                raise RuntimeError(f"Timed out waiting for transcript {transcript_id}")
            time.sleep(2)
            result = request_json(client, "GET", f"/transcript/{transcript_id}")

        if (result.get("redact_pii") is not True
                or result.get("redact_pii_return_unredacted") not in (None, False)
                or not set(POLICIES).issubset(result.get("redact_pii_policies") or [])):
            raise RuntimeError("AssemblyAI did not confirm the required redaction settings")
        if any(result.get(field) for field in
               ("unredacted_text", "unredacted_words", "unredacted_utterances")):
            raise RuntimeError("AssemblyAI returned unexpected unredacted data; refusing to save")
        if not isinstance(result.get("text"), str) or not isinstance(result.get("utterances"), list):
            raise RuntimeError("AssemblyAI returned no transcript text or speaker utterances")
        utterances = []
        for utterance in result["utterances"]:
            if not isinstance(utterance.get("speaker"), str) or not isinstance(utterance.get("text"), str):
                raise RuntimeError("AssemblyAI returned an invalid speaker utterance")
            utterances.append({"speaker": utterance["speaker"], "text": utterance["text"]})
        return {"transcript_id": transcript_id, "text": result["text"], "utterances": utterances}


def write_json(output, data):
    # An interrupted run must not leave a partial output. Files are private by default.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=output.parent,
                                         prefix=".transcript-", delete=False) as destination:
            temporary = destination.name
            json.dump(data, destination, ensure_ascii=False)
            destination.write("\n")
        os.replace(temporary, output)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    api_key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
    if not api_key:
        parser.error("ASSEMBLYAI_API_KEY must be set")
    if not args.audio.is_file() or args.audio.stat().st_size == 0:
        parser.error("audio must be a nonempty local file")
    if args.audio.resolve() == args.output.resolve():
        parser.error("output must differ from the audio file")
    if not args.output.parent.is_dir():
        parser.error("output directory does not exist")
    try:
        write_json(args.output, transcribe(args.audio, api_key))
    except (httpx.HTTPError, OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
        # Do not print library exceptions, which may include sensitive response data.
        message = str(error) if isinstance(error, RuntimeError) else type(error).__name__
        print(f"Transcription failed: {message}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
