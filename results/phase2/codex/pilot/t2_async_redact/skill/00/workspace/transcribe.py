#!/usr/bin/env python3
"""Transcribe calls with server-side PII redaction and two-speaker diarization."""

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request


BASE_URL = "https://api.assemblyai.com/v2"
POLICIES = ["person_name", "phone_number", "us_social_security_number"]
POLL_TIMEOUT = 6 * 60 * 60


class TranscriptionError(Exception):
    pass


def request(api_key, method, path, data=None, headers=None):
    """Never log response bodies: even API error messages can contain PII."""
    request_headers = {"Authorization": api_key, **(headers or {})}
    req = urllib.request.Request(
        BASE_URL + path, data=data, headers=request_headers, method=method
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        raise TranscriptionError(
            f"AssemblyAI {method} {path} failed (HTTP {exc.code})."
        ) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise TranscriptionError(f"AssemblyAI {method} {path}: network failure.") from None
    except (ValueError, UnicodeError):
        raise TranscriptionError("AssemblyAI returned invalid JSON.") from None
    if not isinstance(result, dict):
        raise TranscriptionError("AssemblyAI returned an invalid response.")
    return result


def transcribe(api_key, audio):
    with audio.open("rb") as source:
        uploaded = request(
            api_key, "POST", "/upload", source,
            {"Content-Type": "application/octet-stream",
             "Content-Length": str(os.fstat(source.fileno()).st_size)},
        )
    upload_url = uploaded.get("upload_url")
    if not isinstance(upload_url, str) or not upload_url:
        raise TranscriptionError("AssemblyAI did not return an upload URL.")

    # Redact during the original transcription, so the hosted copy is redacted
    # too. Never request an unredacted companion or submit an unredacted job.
    config = {
        "audio_url": upload_url,
        "speech_models": ["universal-3-5-pro", "universal-2"],
        "speaker_labels": True,
        "speaker_options": {"min_speakers_expected": 2, "max_speakers_expected": 2},
        "redact_pii": True,
        "redact_pii_policies": POLICIES,
        "redact_pii_sub": "entity_name",
        "redact_pii_return_unredacted": False,
    }
    result = request(
        api_key, "POST", "/transcript", json.dumps(config).encode("utf-8"),
        {"Content-Type": "application/json"},
    )
    transcript_id = result.get("id")
    if not isinstance(transcript_id, str) or not transcript_id:
        raise TranscriptionError("AssemblyAI did not return a transcript ID.")
    print(f"AssemblyAI transcript ID: {transcript_id}", file=sys.stderr)
    deadline = time.monotonic() + POLL_TIMEOUT
    while True:
        status = result.get("status")
        if status == "completed":
            break
        if status == "error":
            raise TranscriptionError(
                f"AssemblyAI transcription failed; review transcript {transcript_id}."
            )
        if status not in ("queued", "processing"):
            raise TranscriptionError("AssemblyAI returned an unexpected job status.")
        if time.monotonic() >= deadline:
            raise TranscriptionError(
                f"Polling timed out; transcript {transcript_id} remains in AssemblyAI."
            )
        time.sleep(3)
        result = request(
            api_key, "GET", "/transcript/" + urllib.parse.quote(transcript_id, safe="")
        )

    # Fail closed if the server reports redaction was disabled or unredacted
    # output was requested. Do not persist the full API response or word data.
    if result.get("redact_pii") is not True:
        raise TranscriptionError("AssemblyAI did not confirm PII redaction.")
    if (result.get("redact_pii_return_unredacted", False)
            or any(result.get("unredacted_" + field)
                   for field in ("text", "words", "utterances"))):
        raise TranscriptionError("AssemblyAI unexpectedly enabled unredacted output.")
    if not set(POLICIES).issubset(result.get("redact_pii_policies") or []):
        raise TranscriptionError("AssemblyAI did not confirm all required PII policies.")
    text = result.get("text")
    utterances = result.get("utterances")
    if not isinstance(text, str) or not isinstance(utterances, list):
        raise TranscriptionError("AssemblyAI returned missing transcript text or utterances.")
    cleaned = []
    for utterance in utterances:
        if (not isinstance(utterance, dict)
                or not isinstance(utterance.get("speaker"), str)
                or not isinstance(utterance.get("text"), str)):
            raise TranscriptionError("AssemblyAI returned an invalid utterance.")
        cleaned.append({"speaker": utterance["speaker"], "text": utterance["text"]})
    return {"transcript_id": transcript_id, "text": text, "utterances": cleaned}


def write_output(output, result):
    # Private, atomic output: errors cannot leave a partial transcript file.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=output.parent, prefix=".transcript-",
            suffix=".tmp", delete=False,
        ) as target:
            temporary = Path(target.name)
            json.dump(result, target, ensure_ascii=False)
            target.write("\n")
        os.replace(temporary, output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    api_key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
    if not api_key:
        raise TranscriptionError("Set ASSEMBLYAI_API_KEY in your environment.")
    if not args.audio.is_file() or args.audio.stat().st_size == 0:
        raise TranscriptionError("Input audio must be a nonempty file.")
    if args.audio.resolve() == args.output.resolve():
        raise TranscriptionError("Input and output must be different files.")
    if not args.output.parent.is_dir() or args.output.is_dir():
        raise TranscriptionError("Output must be a file in an existing directory.")
    write_output(args.output, transcribe(api_key, args.audio))


if __name__ == "__main__":
    try:
        main()
    except TranscriptionError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    except OSError:
        print("Error: could not read input or write output.", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("Interrupted; any submitted transcript remains in AssemblyAI.", file=sys.stderr)
        sys.exit(130)
