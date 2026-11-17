#!/usr/bin/env python3
"""Submit audio with server-side PII redaction; retain the remote transcript."""

import json
import os
from pathlib import Path
import sys
import tempfile
import time
import urllib.error
import urllib.request


API = "https://api.assemblyai.com/v2"
POLICIES = ["person_name", "phone_number", "us_social_security_number"]
MAX_WAIT_SECONDS = 3600


class TranscriptionError(Exception):
    pass


def request(key, method, path, data=None, content_type="application/json"):
    req = urllib.request.Request(
        API + path,
        data=data,
        method=method,
        headers={"authorization": key, "content-type": content_type},
    )
    # Never log bodies: even error responses can contain customer information.
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        raise TranscriptionError(
            f"AssemblyAI {method} request failed (HTTP {exc.code})."
        ) from None
    except (OSError, ValueError) as exc:
        raise TranscriptionError(
            f"AssemblyAI {method} request failed ({type(exc).__name__})."
        ) from None
    if not isinstance(result, dict):
        raise TranscriptionError("AssemblyAI returned an invalid response.")
    return result


def transcribe(key, audio):
    with audio.open("rb") as source:
        # Stream the recording instead of loading the whole call into memory.
        upload = request(key, "POST", "/upload", source, "application/octet-stream")
    audio_url = upload.get("upload_url")
    if not isinstance(audio_url, str) or not audio_url:
        raise TranscriptionError("AssemblyAI did not return an upload URL.")

    config = {
        "audio_url": audio_url,
        "speech_models": ["universal-2"],
        "language_code": "en_us",
        "speaker_labels": True,
        "speakers_expected": 2,
        "redact_pii": True,
        "redact_pii_policies": POLICIES,
        "redact_pii_sub": "entity_name",
        "redact_pii_return_unredacted": False,
    }
    transcript = request(key, "POST", "/transcript", json.dumps(config).encode())
    transcript_id = transcript.get("id")
    if not isinstance(transcript_id, str) or not transcript_id:
        raise TranscriptionError("AssemblyAI did not return a transcript ID.")
    # The ID permits review/retention even if polling or local output later fails.
    print(f"AssemblyAI transcript_id: {transcript_id}", file=sys.stderr)
    from urllib.parse import quote

    path = "/transcript/" + quote(transcript_id, safe="")
    deadline = time.monotonic() + MAX_WAIT_SECONDS
    while transcript.get("status") != "completed":
        status = transcript.get("status")
        if status == "error":
            raise TranscriptionError("AssemblyAI transcription failed; review the transcript ID.")
        if status not in ("queued", "processing"):
            raise TranscriptionError("AssemblyAI returned an unexpected transcription status.")
        if time.monotonic() >= deadline:
            raise TranscriptionError("Timed out waiting for AssemblyAI; transcript remains available for review.")
        time.sleep(3)
        transcript = request(key, "GET", path)

    # Fail closed if the server does not confirm the requested privacy settings.
    if (
        transcript.get("redact_pii") is not True
        or not set(POLICIES).issubset(transcript.get("redact_pii_policies") or [])
        or transcript.get("redact_pii_return_unredacted") is True
        or any(transcript.get(field) is not None for field in (
            "unredacted_text", "unredacted_words", "unredacted_utterances"
        ))
    ):
        raise TranscriptionError("AssemblyAI did not confirm PII redaction; no output written.")
    text = transcript.get("text")
    utterances = transcript.get("utterances")
    if not isinstance(text, str) or not isinstance(utterances, list):
        raise TranscriptionError("AssemblyAI returned invalid text or speaker utterances.")
    output_utterances = []
    for utterance in utterances:
        if (
            not isinstance(utterance, dict)
            or not isinstance(utterance.get("speaker"), str)
            or not isinstance(utterance.get("text"), str)
        ):
            raise TranscriptionError("AssemblyAI returned an invalid speaker utterance.")
        output_utterances.append({"speaker": utterance["speaker"], "text": utterance["text"]})
    return {"transcript_id": transcript_id, "text": text, "utterances": output_utterances}


def write_output(destination, result):
    # Atomic replacement means failure cannot leave a partially written transcript.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=destination.parent,
            prefix=".transcript-", suffix=".tmp", delete=False,
        ) as output:
            temporary = output.name
            json.dump(result, output, ensure_ascii=False)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary is not None and os.path.exists(temporary):
            os.unlink(temporary)


def main():
    if len(sys.argv) != 3:
        raise TranscriptionError("Usage: ./run.sh <audio.wav> <out.json>")
    key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
    if not key:
        raise TranscriptionError("ASSEMBLYAI_API_KEY must be set.")
    audio, destination = map(Path, sys.argv[1:])
    if not audio.is_file() or audio.stat().st_size == 0:
        raise TranscriptionError("Input audio must be a nonempty file.")
    if audio.resolve() == destination.resolve():
        raise TranscriptionError("Output must differ from the input audio.")
    if not destination.parent.is_dir() or destination.is_dir():
        raise TranscriptionError("Output must name a file in an existing directory.")
    write_output(destination, transcribe(key, audio))


if __name__ == "__main__":
    try:
        main()
    except (TranscriptionError, OSError) as exc:
        # Filesystem error details are suppressed as paths may contain PII.
        message = str(exc) if isinstance(exc, TranscriptionError) else type(exc).__name__
        print(f"Error: {message}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("Interrupted; any submitted transcript remains in AssemblyAI.", file=sys.stderr)
        sys.exit(130)
