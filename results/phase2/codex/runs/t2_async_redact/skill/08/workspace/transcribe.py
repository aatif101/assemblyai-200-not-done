#!/usr/bin/env python3
"""Transcribe a support call with server-side PII redaction and diarization."""

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
POLL_INTERVAL = 3
POLL_TIMEOUT = 3600


class Failure(Exception):
    """A safe error message that contains no transcript or credential data."""


def request_json(key, method, endpoint, *, body=None, content_type="application/json",
                 content_length=None):
    headers = {"Authorization": key, "Content-Type": content_type}
    if content_length is not None:
        headers["Content-Length"] = str(content_length)
    request = urllib.request.Request(
        BASE_URL + endpoint, data=body, headers=headers, method=method
    )
    # Never replay submissions: a timed-out POST may have created a transcript.
    attempts = 4 if method == "GET" else 1
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                result = json.load(response)
            if not isinstance(result, dict):
                raise Failure("AssemblyAI returned an invalid response.")
            return result
        except urllib.error.HTTPError as exc:
            status = exc.code
            exc.close()
            if status not in (429, 500, 502, 503, 504) or attempt == attempts - 1:
                raise Failure(f"AssemblyAI {method} {endpoint} failed (HTTP {status}).") from None
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if attempt == attempts - 1:
                raise Failure(f"AssemblyAI {method} {endpoint} failed (connection or timeout).") from None
        except (ValueError, UnicodeError):
            raise Failure("AssemblyAI returned invalid JSON.") from None
        time.sleep(2 ** (attempt + 1))


def transcribe(key, audio):
    with audio.open("rb") as source:
        upload = request_json(
            key, "POST", "/upload", body=source,
            content_type="application/octet-stream",
            content_length=os.fstat(source.fileno()).st_size,
        )
    if not isinstance(upload.get("upload_url"), str) or not upload["upload_url"]:
        raise Failure("AssemblyAI did not return an upload URL.")

    config = {
        "audio_url": upload["upload_url"],
        "speech_models": ["universal-3-5-pro", "universal-2"],
        "speaker_labels": True,
        "speaker_options": {"min_speakers_expected": 2, "max_speakers_expected": 2},
        "redact_pii": True,
        "redact_pii_policies": POLICIES,
        "redact_pii_sub": "entity_name",
        # Keep the server's retained transcript redacted as well as the response.
        "redact_pii_return_unredacted": False,
    }
    result = request_json(key, "POST", "/transcript", body=json.dumps(config).encode())
    transcript_id = result.get("id")
    if not isinstance(transcript_id, str) or not transcript_id:
        raise Failure("AssemblyAI did not return a transcript ID.")
    print(f"AssemblyAI transcript_id: {transcript_id}", file=sys.stderr)
    endpoint = "/transcript/" + urllib.parse.quote(transcript_id, safe="")
    deadline = time.monotonic() + POLL_TIMEOUT
    while True:
        if result.get("status") == "completed":
            break
        if result.get("status") == "error":
            # Provider error bodies can include sensitive source material.
            raise Failure(f"AssemblyAI transcription failed; review transcript {transcript_id}.")
        if result.get("status") not in ("queued", "processing"):
            raise Failure("AssemblyAI returned an unexpected transcription status.")
        if time.monotonic() >= deadline:
            raise Failure(f"Timed out waiting for transcript {transcript_id}; it remains on AssemblyAI.")
        time.sleep(POLL_INTERVAL)
        result = request_json(key, "GET", endpoint)

    return export_result(transcript_id, result)


def export_result(transcript_id, result):
    """Export only redacted transcript fields; never serialize the raw response."""
    if result.get("redact_pii") is not True:
        raise Failure("AssemblyAI did not confirm PII redaction; refusing to save.")
    if not set(POLICIES).issubset(result.get("redact_pii_policies") or []):
        raise Failure("AssemblyAI did not confirm all required PII policies; refusing to save.")
    if result.get("redact_pii_return_unredacted") is True or any(
        result.get(field) is not None
        for field in ("unredacted_text", "unredacted_words", "unredacted_utterances")
    ):
        raise Failure("AssemblyAI returned unredacted data; refusing to save.")
    text = result.get("text")
    utterances = result.get("utterances")
    if not isinstance(text, str) or not isinstance(utterances, list):
        raise Failure("AssemblyAI did not return text and speaker utterances.")
    exported = []
    for utterance in utterances:
        if (not isinstance(utterance, dict)
                or not isinstance(utterance.get("speaker"), str)
                or not utterance["speaker"]
                or not isinstance(utterance.get("text"), str)):
            raise Failure("AssemblyAI returned an invalid speaker utterance.")
        exported.append({"speaker": utterance["speaker"], "text": utterance["text"]})
    if text and not exported:
        raise Failure("AssemblyAI returned speech without speaker utterances.")
    return {"transcript_id": transcript_id, "text": text, "utterances": exported}


def write_output(destination, result):
    temporary = None
    try:
        # A restrictive temporary file and atomic rename prevent partial output.
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
    if not key:
        raise Failure("Set ASSEMBLYAI_API_KEY in the environment.")
    if not args.audio.is_file() or args.audio.stat().st_size == 0:
        raise Failure("Input audio must be a nonempty file.")
    if args.audio.resolve() == args.output.resolve():
        raise Failure("Input audio and output JSON must be different files.")
    if not args.output.parent.is_dir() or args.output.is_dir():
        raise Failure("Output must be a file path in an existing directory.")
    result = transcribe(key, args.audio)
    write_output(args.output, result)


if __name__ == "__main__":
    try:
        main()
    except Failure as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    except OSError:
        print("Error: a file or network operation failed.", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("Interrupted; any submitted transcript remains on AssemblyAI.", file=sys.stderr)
        sys.exit(130)
