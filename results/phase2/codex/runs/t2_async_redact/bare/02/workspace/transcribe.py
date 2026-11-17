#!/usr/bin/env python3
"""Upload, transcribe with server-side PII redaction, and save a minimal result."""

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
POLICIES = ["us_social_security_number", "phone_number", "person_name"]
POLL_SECONDS = 3
WAIT_SECONDS = 3600


class TranscriptionError(Exception):
    pass


def request(api_key, method, path, *, payload=None, audio=None):
    headers = {"Authorization": api_key}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    elif audio is not None:
        data = audio
        headers["Content-Type"] = "application/octet-stream"
        headers["Content-Length"] = str(os.fstat(audio.fileno()).st_size)
    req = urllib.request.Request(API + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        # API bodies may contain sensitive input. Never log them.
        raise TranscriptionError(f"AssemblyAI {method} failed (HTTP {exc.code}).") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise TranscriptionError(f"AssemblyAI {method} failed due to a network error.") from None
    except (ValueError, UnicodeError):
        raise TranscriptionError("AssemblyAI returned an invalid JSON response.") from None
    if not isinstance(result, dict):
        raise TranscriptionError("AssemblyAI returned an unexpected response.")
    return result


def require_string(value, field):
    if not isinstance(value, str) or not value:
        raise TranscriptionError(f"AssemblyAI returned an invalid {field}.")
    return value


def transcribe(api_key, audio_path):
    with audio_path.open("rb") as audio:
        if os.fstat(audio.fileno()).st_size == 0:
            raise TranscriptionError("The audio file is empty.")
        upload = request(api_key, "POST", "/upload", audio=audio)
    audio_url = require_string(upload.get("upload_url"), "upload URL")
    submitted = request(api_key, "POST", "/transcript", payload={
        "audio_url": audio_url,
        "speech_models": ["universal-2"],
        "speaker_labels": True,
        "speakers_expected": 2,
        "redact_pii": True,
        "redact_pii_policies": POLICIES,
        "redact_pii_sub": "entity_name",
        "redact_pii_return_unredacted": False,
    })
    transcript_id = require_string(submitted.get("id"), "transcript ID")
    # The ID lets the existing retention job find this transcript even if polling fails.
    print(f"AssemblyAI transcript ID: {transcript_id}", file=sys.stderr)
    deadline = time.monotonic() + WAIT_SECONDS
    path = "/transcript/" + urllib.parse.quote(transcript_id, safe="")
    while True:
        transcript = request(api_key, "GET", path)
        status = transcript.get("status")
        if status == "completed":
            break
        if status == "error":
            raise TranscriptionError("AssemblyAI transcription failed; review it using the transcript ID.")
        if status not in ("queued", "processing"):
            raise TranscriptionError("AssemblyAI returned an unexpected transcription status.")
        if time.monotonic() >= deadline:
            raise TranscriptionError("Timed out waiting for AssemblyAI; the transcript remains there for review.")
        time.sleep(POLL_SECONDS)

    # Fail closed if the service did not apply the requested privacy settings.
    if (transcript.get("id") != transcript_id
            or transcript.get("redact_pii") is not True
            or not set(POLICIES).issubset(transcript.get("redact_pii_policies") or [])
            or transcript.get("redact_pii_return_unredacted") is True
            or any(transcript.get(field) is not None for field in
                   ("unredacted_text", "unredacted_words", "unredacted_utterances"))):
        raise TranscriptionError("AssemblyAI did not confirm the required PII redaction settings.")
    text = transcript.get("text")
    utterances = transcript.get("utterances")
    if not isinstance(text, str) or not isinstance(utterances, list):
        raise TranscriptionError("AssemblyAI did not return text and speaker utterances.")
    result = []
    for utterance in utterances:
        if (not isinstance(utterance, dict)
                or not isinstance(utterance.get("text"), str)):
            raise TranscriptionError("AssemblyAI returned an invalid utterance.")
        speaker = require_string(utterance.get("speaker"), "speaker label")
        result.append({"speaker": speaker, "text": utterance["text"]})
    return {"transcript_id": transcript_id, "text": text, "utterances": result}


def save_result(output_path, result):
    # A same-directory rename prevents partial output; mkstemp creates mode 0600.
    fd, temporary = tempfile.mkstemp(prefix=".transcript-", suffix=".json", dir=output_path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(result, output, ensure_ascii=False)
            output.write("\n")
        os.replace(temporary, output_path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main():
    if len(sys.argv) != 3:
        print("Usage: ./run.sh <audio.wav> <out.json>", file=sys.stderr)
        return 2
    api_key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
    if not api_key:
        print("Error: ASSEMBLYAI_API_KEY must be set.", file=sys.stderr)
        return 1
    audio_path, output_path = map(Path, sys.argv[1:])
    try:
        if not audio_path.is_file():
            raise TranscriptionError("The input audio file does not exist or is not a regular file.")
        if audio_path.resolve() == output_path.resolve():
            raise TranscriptionError("Input and output must be different files.")
        if not output_path.parent.is_dir():
            raise TranscriptionError("The output directory does not exist.")
        result = transcribe(api_key, audio_path)
        save_result(output_path, result)
    except TranscriptionError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except OSError:
        print("Error: could not read the audio file or write the output file.", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrupted; any submitted transcript remains in AssemblyAI for review.", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
