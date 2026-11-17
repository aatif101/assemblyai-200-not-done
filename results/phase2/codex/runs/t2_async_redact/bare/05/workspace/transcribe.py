#!/usr/bin/env python3
"""Transcribe with AssemblyAI's server-side PII redaction and diarization."""

import argparse
import http.client
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from urllib.parse import quote


API_HOST = "api.assemblyai.com"
POLL_TIMEOUT = 3600


class Failure(Exception):
    pass


def request(key, method, path, *, payload=None, audio=None):
    headers = {"Authorization": key, "Accept": "application/json"}
    body = None
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    elif audio is not None:
        body = audio
        headers["Content-Type"] = "application/octet-stream"
        headers["Content-Length"] = str(os.fstat(audio.fileno()).st_size)

    connection = http.client.HTTPSConnection(API_HOST, timeout=120)
    try:
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        raw = response.read()
        if not 200 <= response.status < 300:
            # API response bodies can contain sensitive information. Do not log them.
            raise Failure(f"AssemblyAI {method} {path} failed (HTTP {response.status}).")
        try:
            result = json.loads(raw)
        except (ValueError, UnicodeError):
            raise Failure("AssemblyAI returned invalid JSON.") from None
        if not isinstance(result, dict):
            raise Failure("AssemblyAI returned an unexpected response.")
        return result
    finally:
        connection.close()


def required_string(data, field):
    value = data.get(field)
    if not isinstance(value, str) or not value:
        raise Failure(f"AssemblyAI response is missing {field}.")
    return value


def transcribe(key, source):
    with source.open("rb") as audio:
        upload = request(key, "POST", "/v2/upload", audio=audio)
    audio_url = required_string(upload, "upload_url")
    submitted = request(key, "POST", "/v2/transcript", payload={
        "audio_url": audio_url,
        "speech_models": ["universal-2"],
        "language_code": "en_us",
        "speaker_labels": True,
        "speakers_expected": 2,
        "redact_pii": True,
        "redact_pii_policies": [
            "person_name", "phone_number", "us_social_security_number",
        ],
        "redact_pii_sub": "entity_name",
    })
    transcript_id = required_string(submitted, "id")
    # Retain the ID for review/retention even if polling later fails. Never DELETE.
    print(f"AssemblyAI transcript_id: {transcript_id}", file=sys.stderr)
    path = "/v2/transcript/" + quote(transcript_id, safe="")
    deadline = time.monotonic() + POLL_TIMEOUT
    while True:
        result = request(key, "GET", path)
        status = result.get("status")
        if status == "completed":
            break
        if status == "error":
            raise Failure("AssemblyAI transcription failed; review it using the transcript_id.")
        if status not in ("queued", "processing"):
            raise Failure("AssemblyAI returned an unexpected transcription status.")
        if time.monotonic() >= deadline:
            raise Failure("Timed out waiting for AssemblyAI; the transcript is retained for review.")
        time.sleep(3)

    # Fail closed if the server did not retain the requested redaction settings.
    policies = result.get("redact_pii_policies")
    required_policies = {"person_name", "phone_number", "us_social_security_number"}
    if (result.get("redact_pii") is not True
            or not isinstance(policies, list)
            or not required_policies.issubset(policies)):
        raise Failure("AssemblyAI did not confirm the required server-side PII redaction.")
    if result.get("id") != transcript_id:
        raise Failure("AssemblyAI returned an unexpected transcript ID.")
    text = result.get("text")
    utterances = result.get("utterances")
    if not isinstance(text, str) or not isinstance(utterances, list):
        raise Failure("AssemblyAI did not return text and speaker utterances.")
    clean_utterances = []
    for utterance in utterances:
        if (not isinstance(utterance, dict)
                or not isinstance(utterance.get("speaker"), str)
                or not isinstance(utterance.get("text"), str)):
            raise Failure("AssemblyAI returned an invalid speaker utterance.")
        clean_utterances.append({"speaker": utterance["speaker"], "text": utterance["text"]})
    return {"transcript_id": transcript_id, "text": text, "utterances": clean_utterances}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
    if not key:
        raise Failure("Set ASSEMBLYAI_API_KEY in the environment.")
    source = args.audio.resolve(strict=True)
    output = args.output.resolve()
    if not source.is_file() or source.stat().st_size == 0:
        raise Failure("The audio input must be a nonempty regular file.")
    if source == output or (output.exists() and os.path.samefile(source, output)):
        raise Failure("The output must be different from the audio input.")

    # Check the destination before uploading. Only publish a complete success result.
    descriptor, temporary = tempfile.mkstemp(prefix=".transcript-", suffix=".json", dir=output.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            result = transcribe(key, source)
            json.dump(result, handle, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, output)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Interrupted; any submitted AssemblyAI transcript is retained for review.", file=sys.stderr)
        sys.exit(130)
    except (Failure, OSError, http.client.HTTPException) as exc:
        # Avoid exposing API keys, audio, or unredacted API response data in logs.
        message = str(exc) if isinstance(exc, Failure) else type(exc).__name__
        print(f"Error: {message}", file=sys.stderr)
        sys.exit(1)
