#!/usr/bin/env python3
"""Submit a call with server-side PII redaction and two-speaker diarization."""

import json
import os
from pathlib import Path
import sys
import tempfile
import time
import urllib.error
import urllib.request


BASE = "https://api.assemblyai.com/v2"
POLICIES = ["person_name", "phone_number", "us_social_security_number"]


class Failure(Exception):
    pass


def request(key, method, path, data=None, headers=None):
    req = urllib.request.Request(
        BASE + path,
        data=data,
        method=method,
        headers={"authorization": key, **(headers or {})},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        # Response bodies can contain customer data; never print them.
        raise Failure(f"AssemblyAI {method} {path}: HTTP {exc.code}") from None
    except (OSError, ValueError) as exc:
        raise Failure(f"AssemblyAI {method} {path}: request failed ({type(exc).__name__})") from None
    if not isinstance(result, dict):
        raise Failure("AssemblyAI returned an invalid response")
    return result


def transcribe(audio, key):
    with audio.open("rb") as source:
        uploaded = request(key, "POST", "/upload", source, {
            "Content-Type": "application/octet-stream",
            "Content-Length": str(os.fstat(source.fileno()).st_size),
        })
    if not isinstance(uploaded.get("upload_url"), str):
        raise Failure("AssemblyAI did not return an upload URL")

    config = {
        "audio_url": uploaded["upload_url"],
        "speech_models": ["universal-2"],
        "language_code": "en_us",
        "speaker_labels": True,
        "speakers_expected": 2,
        "redact_pii": True,
        "redact_pii_policies": POLICIES,
        "redact_pii_sub": "entity_name",
        # Do not retain an unredacted transcript alongside the redacted one.
        "redact_pii_return_unredacted": False,
    }
    transcript = request(key, "POST", "/transcript", json.dumps(config).encode(), {
        "Content-Type": "application/json",
    })
    transcript_id = transcript.get("id")
    if not isinstance(transcript_id, str) or not transcript_id or not all(
        c.isascii() and (c.isalnum() or c == "-") for c in transcript_id
    ):
        raise Failure("AssemblyAI did not return a valid transcript ID")
    print(f"AssemblyAI transcript: {transcript_id}", file=sys.stderr)
    deadline = time.monotonic() + 7200
    while transcript.get("status") != "completed":
        if transcript.get("status") == "error":
            raise Failure(f"AssemblyAI transcription failed (ID: {transcript_id})")
        if transcript.get("status") not in ("queued", "processing"):
            raise Failure(f"AssemblyAI returned an unexpected status (ID: {transcript_id})")
        if time.monotonic() >= deadline:
            raise Failure(f"Timed out waiting for transcript {transcript_id}; it remains in AssemblyAI")
        time.sleep(2)
        transcript = request(key, "GET", "/transcript/" + transcript_id)

    # Fail closed if the service did not honor the requested privacy settings.
    if (transcript.get("redact_pii") is not True
            or set(transcript.get("redact_pii_policies") or []) != set(POLICIES)
            or transcript.get("redact_pii_return_unredacted") not in (None, False)
            or any(transcript.get(field) for field in
                   ("unredacted_text", "unredacted_words", "unredacted_utterances"))):
        raise Failure(f"AssemblyAI did not confirm PII redaction (ID: {transcript_id})")
    text = transcript.get("text")
    utterances = transcript.get("utterances")
    if not isinstance(text, str) or not isinstance(utterances, list):
        raise Failure(f"AssemblyAI returned incomplete transcript data (ID: {transcript_id})")
    result = []
    for utterance in utterances:
        if (not isinstance(utterance, dict)
                or not isinstance(utterance.get("speaker"), str)
                or not isinstance(utterance.get("text"), str)):
            raise Failure(f"AssemblyAI returned invalid utterances (ID: {transcript_id})")
        result.append({"speaker": utterance["speaker"], "text": utterance["text"]})
    return {"transcript_id": transcript_id, "text": text, "utterances": result}


def main():
    if len(sys.argv) != 3:
        raise Failure("Usage: ./run.sh <audio.wav> <out.json>")
    key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
    if not key:
        raise Failure("ASSEMBLYAI_API_KEY is required")
    audio, output = map(Path, sys.argv[1:])
    if not audio.is_file():
        raise Failure("Input audio must be an existing file")
    if audio.resolve() == output.resolve():
        raise Failure("Input audio and output JSON must be different files")
    if not output.parent.is_dir():
        raise Failure("Output directory does not exist")
    result = transcribe(audio, key)
    # Publish only a complete, redacted result; restrict file permissions.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=output.parent,
                                         prefix=".transcript-", delete=False) as handle:
            temporary = handle.name
            json.dump(result, handle, ensure_ascii=False)
            handle.write("\n")
        os.replace(temporary, output)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


if __name__ == "__main__":
    try:
        main()
    except (Failure, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("Interrupted; any submitted transcript remains in AssemblyAI", file=sys.stderr)
        sys.exit(130)
