#!/usr/bin/env python3
"""Transcribe through AssemblyAI with server-side PII redaction."""

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


API = "https://api.assemblyai.com/v2"
POLICIES = ["person_name", "phone_number", "us_social_security_number"]
POLL_TIMEOUT = 3600


class TranscriptionError(Exception):
    pass


def request(key, method, path, data=None, headers=None):
    req = urllib.request.Request(
        API + path,
        data=data,
        method=method,
        headers={"Authorization": key, **(headers or {})},
    )
    # Do not log response bodies: errors may contain sensitive input.
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        raise TranscriptionError(f"AssemblyAI {method} {path}: HTTP {exc.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise TranscriptionError(f"AssemblyAI {method} request failed (network or timeout)") from None
    except (ValueError, UnicodeError):
        raise TranscriptionError("AssemblyAI returned invalid JSON") from None
    if not isinstance(result, dict):
        raise TranscriptionError("AssemblyAI returned an unexpected response")
    return result


def json_request(key, path, payload):
    return request(
        key, "POST", path, json.dumps(payload).encode("utf-8"),
        {"Content-Type": "application/json"},
    )


def transcribe(key, audio):
    with audio.open("rb") as source:
        uploaded = request(key, "POST", "/upload", source, {
            "Content-Type": "application/octet-stream",
            "Content-Length": str(os.fstat(source.fileno()).st_size),
        })
    upload_url = uploaded.get("upload_url")
    if not isinstance(upload_url, str) or not upload_url.startswith("https://"):
        raise TranscriptionError("AssemblyAI did not return a valid upload URL")

    submitted = json_request(key, "/transcript", {
        "audio_url": upload_url,
        "speaker_labels": True,
        "speakers_expected": 2,
        "redact_pii": True,
        "redact_pii_policies": POLICIES,
        "redact_pii_sub": "entity_name",
    })
    transcript_id = submitted.get("id")
    if not isinstance(transcript_id, str) or not transcript_id:
        raise TranscriptionError("AssemblyAI did not return a transcript ID")
    print(f"AssemblyAI transcript ID: {transcript_id}", file=sys.stderr)
    endpoint = "/transcript/" + urllib.parse.quote(transcript_id, safe="")
    deadline = time.monotonic() + POLL_TIMEOUT
    while True:
        result = request(key, "GET", endpoint)
        status = result.get("status")
        if status == "completed":
            break
        if status == "error":
            raise TranscriptionError("AssemblyAI transcription failed; review it using the transcript ID")
        if status not in ("queued", "processing"):
            raise TranscriptionError("AssemblyAI returned an unexpected transcript status")
        if time.monotonic() >= deadline:
            raise TranscriptionError("Transcription timed out; the AssemblyAI transcript is retained")
        time.sleep(3)

    # Require confirmation of server-side redaction before persisting any text.
    if result.get("redact_pii") is not True:
        raise TranscriptionError("AssemblyAI did not apply the requested PII redaction")
    policies = result.get("redact_pii_policies")
    if not isinstance(policies, list) or not all(isinstance(p, str) for p in policies) or not set(POLICIES).issubset(policies):
        raise TranscriptionError("AssemblyAI returned incomplete PII redaction policies")
    if not isinstance(result.get("text"), str) or not isinstance(result.get("utterances"), list):
        raise TranscriptionError("AssemblyAI did not return text and speaker utterances")
    utterances = []
    for utterance in result["utterances"]:
        if not isinstance(utterance, dict) or not isinstance(utterance.get("text"), str) or utterance.get("speaker") is None:
            raise TranscriptionError("AssemblyAI returned an invalid speaker utterance")
        utterances.append({"speaker": str(utterance["speaker"]), "text": utterance["text"]})
    return {"transcript_id": transcript_id, "text": result["text"], "utterances": utterances}


def write_output(destination, result):
    # Atomic replacement prevents a partial output file on a failed write.
    fd, temporary = tempfile.mkstemp(prefix=".transcript-", suffix=".json", dir=destination.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(result, output, ensure_ascii=False)
            output.write("\n")
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
    if not key:
        raise TranscriptionError("Set ASSEMBLYAI_API_KEY in the environment")
    if not args.audio.is_file() or args.audio.stat().st_size == 0:
        raise TranscriptionError("Audio must be a nonempty readable file")
    if args.audio.resolve() == args.output.resolve():
        raise TranscriptionError("Output must differ from the input recording")
    if not args.output.parent.is_dir():
        raise TranscriptionError("Output directory does not exist")
    write_output(args.output, transcribe(key, args.audio))


if __name__ == "__main__":
    try:
        main()
    except (TranscriptionError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("Interrupted; any submitted AssemblyAI transcript is retained.", file=sys.stderr)
        sys.exit(130)
