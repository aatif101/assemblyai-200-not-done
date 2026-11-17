#!/usr/bin/env python3
"""Transcribe through AssemblyAI with server-side PII redaction."""

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
POLICIES = ["person_name", "phone_number", "us_social_security_number"]


class Failure(Exception):
    pass


def request(key, method, endpoint, *, payload=None, audio=None):
    headers = {"authorization": key}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    elif audio is not None:
        data = audio
        headers["Content-Type"] = "application/octet-stream"
        headers["Content-Length"] = str(os.fstat(audio.fileno()).st_size)
    req = urllib.request.Request(API + endpoint, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        # Response bodies can contain transcript content. Never log them.
        raise Failure(f"AssemblyAI {method} {endpoint}: HTTP {exc.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        raise Failure(f"AssemblyAI {method} {endpoint}: network or response error") from None
    if not isinstance(result, dict):
        raise Failure("AssemblyAI returned an invalid response")
    return result


def output_transcript(result, transcript_id):
    # Only accept a result that confirms server-side redaction was enabled.
    if result.get("redact_pii") is not True:
        raise Failure("AssemblyAI did not confirm PII redaction")
    if not set(POLICIES).issubset(result.get("redact_pii_policies") or []):
        raise Failure("AssemblyAI did not confirm all required PII policies")
    if result.get("redact_pii_return_unredacted") is True or any(
        result.get(field) is not None
        for field in ("unredacted_text", "unredacted_words", "unredacted_utterances")
    ):
        raise Failure("AssemblyAI unexpectedly returned unredacted data")
    text = result.get("text")
    utterances = result.get("utterances")
    if not isinstance(text, str) or not isinstance(utterances, list):
        raise Failure("AssemblyAI returned no transcript text or speaker utterances")
    clean_utterances = []
    for utterance in utterances:
        if (
            not isinstance(utterance, dict)
            or not isinstance(utterance.get("speaker"), str)
            or not isinstance(utterance.get("text"), str)
        ):
            raise Failure("AssemblyAI returned an invalid speaker utterance")
        clean_utterances.append({"speaker": utterance["speaker"], "text": utterance["text"]})
    return {"transcript_id": transcript_id, "text": text, "utterances": clean_utterances}


def write_output(path, value):
    # Replace atomically, so a failed run never leaves a partial transcript.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=".transcript-", delete=False
        ) as stream:
            temporary = stream.name
            json.dump(value, stream, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and os.path.exists(temporary):
            os.unlink(temporary)


def main():
    if len(sys.argv) != 3:
        raise Failure("Usage: ./run.sh <audio.wav> <out.json>")
    key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
    if not key:
        raise Failure("ASSEMBLYAI_API_KEY is required")
    source = Path(sys.argv[1])
    destination = Path(sys.argv[2])
    if not source.is_file() or source.stat().st_size == 0:
        raise Failure("Input must be a nonempty audio file")
    if source.resolve() == destination.resolve():
        raise Failure("Input and output paths must differ")
    if not destination.parent.is_dir() or destination.is_dir():
        raise Failure("Output must be a file in an existing directory")

    with source.open("rb") as audio:
        uploaded = request(key, "POST", "/upload", audio=audio)
    audio_url = uploaded.get("upload_url")
    if not isinstance(audio_url, str) or not audio_url:
        raise Failure("AssemblyAI returned no upload URL")
    result = request(key, "POST", "/transcript", payload={
        "audio_url": audio_url,
        "speech_models": ["universal-2"],
        "language_code": "en_us",
        "speaker_labels": True,
        "speakers_expected": 2,
        "redact_pii": True,
        "redact_pii_policies": POLICIES,
        "redact_pii_sub": "entity_name",
        "redact_pii_return_unredacted": False,
    })
    transcript_id = result.get("id")
    if not isinstance(transcript_id, str) or not transcript_id:
        raise Failure("AssemblyAI returned no transcript ID")
    print(f"AssemblyAI transcript: {transcript_id}", file=sys.stderr)
    deadline = time.monotonic() + 3600
    while result.get("status") != "completed":
        if result.get("status") == "error":
            raise Failure(f"AssemblyAI transcription failed (ID: {transcript_id})")
        if result.get("status") not in ("queued", "processing"):
            raise Failure("AssemblyAI returned an unexpected transcription status")
        if time.monotonic() >= deadline:
            raise Failure(f"Timed out waiting for transcription (ID: {transcript_id})")
        time.sleep(3)
        result = request(key, "GET", "/transcript/" + urllib.parse.quote(transcript_id, safe=""))
    write_output(destination, output_transcript(result, transcript_id))


if __name__ == "__main__":
    try:
        main()
    except (Failure, OSError) as exc:
        # Do not print arbitrary filesystem or API payload contents.
        message = str(exc) if isinstance(exc, Failure) else "Local file operation failed"
        print(f"Error: {message}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        sys.exit(130)
