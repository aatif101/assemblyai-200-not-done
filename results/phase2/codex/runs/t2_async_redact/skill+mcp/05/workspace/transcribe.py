#!/usr/bin/env python3
"""Upload a call and save only AssemblyAI's redacted transcript fields."""

import argparse
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request


BASE_URL = "https://api.assemblyai.com/v2"
POLICIES = ["person_name", "phone_number", "us_social_security_number"]


class Failure(Exception):
    pass


def request(key, method, endpoint, data=None, content_type=None):
    headers = {"Authorization": key, "Accept": "application/json"}
    if content_type:
        headers["Content-Type"] = content_type
    req = urllib.request.Request(
        BASE_URL + endpoint, data=data, headers=headers, method=method
    )
    # Retry reads only: retrying submissions can create duplicate transcripts.
    for attempt in range(5 if method == "GET" else 1):
        try:
            with urllib.request.urlopen(req, timeout=120) as response:
                result = json.load(response)
            if not isinstance(result, dict):
                raise Failure("Unexpected API response.")
            return result
        except urllib.error.HTTPError as exc:
            if method == "GET" and exc.code in (429, 500, 502, 503, 504) and attempt < 4:
                exc.close()
                time.sleep(2 ** attempt)
                continue
            # Do not log response bodies: errors can contain sensitive content.
            raise Failure(f"AssemblyAI {method} request failed (HTTP {exc.code}).") from None
        except (urllib.error.URLError, socket.timeout, TimeoutError):
            if method == "GET" and attempt < 4:
                time.sleep(2 ** attempt)
                continue
            raise Failure("AssemblyAI network request failed or timed out.") from None
        except (ValueError, UnicodeError):
            raise Failure("AssemblyAI returned invalid JSON.") from None


def export_result(result, transcript_id):
    if result.get("redact_pii") is not True:
        raise Failure("AssemblyAI did not confirm PII redaction.")
    if not set(POLICIES).issubset(result.get("redact_pii_policies") or []):
        raise Failure("AssemblyAI did not confirm all required redaction policies.")
    if result.get("redact_pii_return_unredacted") or any(
        result.get(field) is not None
        for field in ("unredacted_text", "unredacted_words", "unredacted_utterances")
    ):
        raise Failure("AssemblyAI unexpectedly returned unredacted fields.")
    text = result.get("text")
    utterances = result.get("utterances")
    if not isinstance(text, str) or not isinstance(utterances, list):
        raise Failure("AssemblyAI returned missing transcript or speaker utterances.")
    output_utterances = []
    for utterance in utterances:
        if not isinstance(utterance, dict) or not all(
            isinstance(utterance.get(field), str) for field in ("speaker", "text")
        ):
            raise Failure("AssemblyAI returned an invalid speaker utterance.")
        output_utterances.append({"speaker": utterance["speaker"], "text": utterance["text"]})
    return {"transcript_id": transcript_id, "text": text, "utterances": output_utterances}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
    if not key:
        raise Failure("ASSEMBLYAI_API_KEY is required.")
    if args.audio.resolve() == args.output.resolve():
        raise Failure("Input and output paths must differ.")
    if not args.output.parent.is_dir():
        raise Failure("Output directory does not exist.")
    with args.audio.open("rb") as audio:
        upload = request(key, "POST", "/upload", audio, "application/octet-stream")
    upload_url = upload.get("upload_url")
    if not isinstance(upload_url, str) or not upload_url:
        raise Failure("AssemblyAI did not return an upload URL.")
    config = {
        "audio_url": upload_url,
        "speech_models": ["universal-3-5-pro", "universal-2"],
        "language_code": "en",
        "speaker_labels": True,
        "speakers_expected": 2,
        "redact_pii": True,
        "redact_pii_policies": POLICIES,
        "redact_pii_sub": "entity_name",
        "redact_pii_return_unredacted": False,
    }
    result = request(key, "POST", "/transcript", json.dumps(config).encode(), "application/json")
    transcript_id = result.get("id")
    if not isinstance(transcript_id, str) or not transcript_id:
        raise Failure("AssemblyAI did not return a transcript ID.")
    print(f"Transcript ID: {transcript_id}", file=sys.stderr)
    deadline = time.monotonic() + 24 * 60 * 60
    endpoint = "/transcript/" + urllib.parse.quote(transcript_id, safe="")
    while True:
        result = request(key, "GET", endpoint)
        status = result.get("status")
        if status == "completed":
            break
        if status == "error":
            raise Failure("AssemblyAI transcription failed; review the transcript ID above.")
        if status not in ("queued", "processing"):
            raise Failure("AssemblyAI returned an unexpected transcription status.")
        if time.monotonic() >= deadline:
            raise Failure("Transcription polling timed out; the remote transcript is retained.")
        time.sleep(3)
    output = export_result(result, transcript_id)
    # Atomic replacement prevents a partial result being mistaken for success.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=args.output.parent,
                                         prefix=".transcript-", delete=False) as handle:
            temporary = handle.name
            json.dump(output, handle, ensure_ascii=False)
            handle.write("\n")
        os.replace(temporary, args.output)
        temporary = None
    finally:
        if temporary:
            os.unlink(temporary)


if __name__ == "__main__":
    try:
        main()
    except (Failure, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("Interrupted; any submitted transcript is retained in AssemblyAI.", file=sys.stderr)
        sys.exit(130)
