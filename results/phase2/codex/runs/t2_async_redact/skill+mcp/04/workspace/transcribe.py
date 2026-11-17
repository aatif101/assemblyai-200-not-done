#!/usr/bin/env python3
"""Transcribe support calls with server-side PII redaction and diarization."""

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
POLICIES = ["person_name", "phone_number", "us_social_security_number"]
POLL_TIMEOUT = 3600


class TranscriptionError(Exception):
    pass


def api_request(api_key, method, path, data=None, content_type=None, length=None):
    headers = {"Authorization": api_key}
    if content_type:
        headers["Content-Type"] = content_type
    if length is not None:
        headers["Content-Length"] = str(length)
    request = Request(BASE_URL + path, data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=120) as response:
            result = json.load(response)
    except HTTPError as exc:
        # Do not log response bodies: service errors can contain sensitive text.
        raise TranscriptionError(f"AssemblyAI {method} request failed (HTTP {exc.code}).") from None
    except (URLError, TimeoutError, OSError):
        raise TranscriptionError(f"AssemblyAI {method} request failed due to a network error.") from None
    except (ValueError, UnicodeError):
        raise TranscriptionError("AssemblyAI returned invalid JSON.") from None
    if not isinstance(result, dict):
        raise TranscriptionError("AssemblyAI returned an invalid response.")
    return result


def transcribe(api_key, audio):
    with audio.open("rb") as source:
        upload = api_request(api_key, "POST", "/upload", source,
                             "application/octet-stream", os.fstat(source.fileno()).st_size)
    upload_url = upload.get("upload_url")
    if not isinstance(upload_url, str) or not upload_url:
        raise TranscriptionError("AssemblyAI did not return an upload URL.")

    # Redact at submission, so the provider's final artifact is also redacted.
    # Never submit a second, unredacted transcription or request unredacted fields.
    config = {
        "audio_url": upload_url,
        "speech_models": ["universal-3-5-pro", "universal-2"],
        "language_code": "en_us",
        "speaker_labels": True,
        "speaker_options": {"min_speakers_expected": 2, "max_speakers_expected": 2},
        "redact_pii": True,
        "redact_pii_policies": POLICIES,
        "redact_pii_sub": "entity_name",
        "redact_pii_return_unredacted": False,
    }
    submitted = api_request(api_key, "POST", "/transcript",
                            json.dumps(config).encode(), "application/json")
    transcript_id = submitted.get("id")
    if not isinstance(transcript_id, str) or not transcript_id:
        raise TranscriptionError("AssemblyAI did not return a transcript ID.")
    print(f"AssemblyAI transcript ID: {transcript_id}", file=sys.stderr)
    endpoint = "/transcript/" + quote(transcript_id, safe="")
    deadline = time.monotonic() + POLL_TIMEOUT
    while time.monotonic() < deadline:
        result = api_request(api_key, "GET", endpoint)
        status = result.get("status")
        if status == "completed":
            return format_result(transcript_id, result)
        if status == "error":
            raise TranscriptionError("AssemblyAI transcription failed; review the job using its ID.")
        if status not in ("queued", "processing"):
            raise TranscriptionError("AssemblyAI returned an unexpected transcription status.")
        time.sleep(3)
    raise TranscriptionError("Timed out waiting for AssemblyAI; the job remains available for review.")


def format_result(transcript_id, result):
    # Refuse to persist a response that does not confirm the requested redaction.
    if (result.get("redact_pii") is not True
            or not set(POLICIES).issubset(result.get("redact_pii_policies") or [])
            or result.get("redact_pii_return_unredacted") is True
            or any(result.get(key) is not None for key in
                   ("unredacted_text", "unredacted_words", "unredacted_utterances"))):
        raise TranscriptionError("AssemblyAI did not confirm the required PII redaction; no output written.")
    text = result.get("text")
    utterances = result.get("utterances")
    if not isinstance(text, str) or not isinstance(utterances, list):
        raise TranscriptionError("AssemblyAI did not return text and speaker utterances.")
    clean_utterances = []
    for utterance in utterances:
        if (not isinstance(utterance, dict)
                or not isinstance(utterance.get("speaker"), str)
                or not utterance["speaker"]
                or not isinstance(utterance.get("text"), str)):
            raise TranscriptionError("AssemblyAI returned an invalid speaker utterance.")
        clean_utterances.append({"speaker": utterance["speaker"], "text": utterance["text"]})
    if text.strip() and not clean_utterances:
        raise TranscriptionError("AssemblyAI returned speech without speaker utterances.")
    return {"transcript_id": transcript_id, "text": text, "utterances": clean_utterances}


def main():
    if len(sys.argv) != 3:
        print("Usage: ./run.sh <audio.wav> <out.json>", file=sys.stderr)
        return 2
    api_key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
    if not api_key:
        print("ASSEMBLYAI_API_KEY must be set.", file=sys.stderr)
        return 2
    audio, output = map(Path, sys.argv[1:])
    temporary = None
    try:
        if not audio.is_file() or audio.stat().st_size == 0:
            raise TranscriptionError("Input audio must be a nonempty, readable file.")
        if audio.resolve() == output.resolve() or (output.exists() and os.path.samefile(audio, output)):
            raise TranscriptionError("Input audio and output JSON must be different files.")
        # Check the output directory before incurring an API charge. Atomic replace
        # leaves an existing output intact if transcription or serialization fails.
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=output.parent,
                                         prefix=".transcript-", delete=False) as destination:
            temporary = Path(destination.name)
            result = transcribe(api_key, audio)
            json.dump(result, destination, ensure_ascii=False)
            destination.write("\n")
            destination.flush()
            os.fsync(destination.fileno())
        os.replace(temporary, output)
        return 0
    except (TranscriptionError, OSError) as exc:
        # OSError filenames may be user supplied; avoid logging arbitrary paths.
        message = str(exc) if isinstance(exc, TranscriptionError) else "Local file operation failed."
        print(message, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrupted; any submitted transcript is retained for review.", file=sys.stderr)
        return 130
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
