#!/usr/bin/env python3
"""Upload, diarize, and redact a call using AssemblyAI's stored transcript."""

import argparse
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
POLL_SECONDS = 3
MAX_WAIT_SECONDS = 3600


class Failure(Exception):
    pass


def request(key, method, path, data=None, headers=None):
    # Do not log response bodies: provider errors can contain sensitive text.
    req = urllib.request.Request(
        API + path,
        data=data,
        method=method,
        headers={"Authorization": key, **(headers or {})},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        raise Failure(f"AssemblyAI {method} {path.split('?')[0]} failed (HTTP {exc.code}).") from None
    except (OSError, ValueError, urllib.error.URLError):
        raise Failure("AssemblyAI request failed or returned invalid JSON.") from None
    if not isinstance(result, dict):
        raise Failure("AssemblyAI returned an unexpected response.")
    return result


def transcribe(key, audio):
    with audio.open("rb") as stream:
        uploaded = request(key, "POST", "/upload", stream, {
            "Content-Type": "application/octet-stream",
            "Content-Length": str(os.fstat(stream.fileno()).st_size),
        })
    url = uploaded.get("upload_url")
    if not isinstance(url, str) or not url.startswith("https://"):
        raise Failure("AssemblyAI did not return an upload URL.")

    # Redact at creation, so the retained provider transcript is redacted too.
    payload = {
        "audio_url": url,
        "speaker_labels": True,
        "speakers_expected": 2,
        "redact_pii": True,
        "redact_pii_policies": POLICIES,
        "redact_pii_sub": "entity_name",
    }
    transcript = request(key, "POST", "/transcript", json.dumps(payload).encode(),
                         {"Content-Type": "application/json"})
    transcript_id = transcript.get("id")
    if not isinstance(transcript_id, str) or not transcript_id or any(
        c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-" for c in transcript_id
    ):
        raise Failure("AssemblyAI did not return a valid transcript ID.")
    print(f"AssemblyAI transcript: {transcript_id}", file=sys.stderr)

    deadline = time.monotonic() + MAX_WAIT_SECONDS
    while True:
        status = transcript.get("status")
        if status == "completed":
            break
        if status == "error":
            raise Failure("AssemblyAI transcription failed; review the transcript by its ID.")
        if status not in ("queued", "processing"):
            raise Failure("AssemblyAI returned an unexpected transcription status.")
        if time.monotonic() >= deadline:
            raise Failure("Timed out waiting for AssemblyAI; the transcript remains available for review.")
        time.sleep(POLL_SECONDS)
        transcript = request(key, "GET", f"/transcript/{transcript_id}")

    return output_from_transcript(transcript, transcript_id)


def output_from_transcript(transcript, transcript_id):
    # Refuse to store results if the provider has not confirmed our settings.
    if (transcript.get("id") != transcript_id
            or transcript.get("redact_pii") is not True
            or not set(POLICIES).issubset(transcript.get("redact_pii_policies") or [])
            or transcript.get("speaker_labels") is not True):
        raise Failure("AssemblyAI did not confirm the required redaction and speaker settings.")
    text = transcript.get("text")
    utterances = transcript.get("utterances")
    if not isinstance(text, str) or not isinstance(utterances, list):
        raise Failure("AssemblyAI did not return transcript text and utterances.")
    result = []
    for utterance in utterances:
        if (not isinstance(utterance, dict)
                or not isinstance(utterance.get("speaker"), str)
                or not utterance["speaker"]
                or not isinstance(utterance.get("text"), str)):
            raise Failure("AssemblyAI returned an invalid utterance.")
        result.append({"speaker": utterance["speaker"], "text": utterance["text"]})
    return {"transcript_id": transcript_id, "text": text, "utterances": result}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    temporary = None
    try:
        key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
        if not key:
            raise Failure("Set ASSEMBLYAI_API_KEY before running.")
        if not args.audio.is_file() or args.audio.stat().st_size == 0:
            raise Failure("Input must be a nonempty audio file.")
        if args.audio.resolve() == args.output.resolve():
            raise Failure("Input and output must be different files.")
        # Check destination before creating a billable provider transcript.
        # mkstemp creates a private file (0600); only validated output is written.
        fd, temporary = tempfile.mkstemp(prefix=".transcript-", suffix=".tmp", dir=args.output.parent)
        os.close(fd)
        result = transcribe(key, args.audio)
        with open(temporary, "w", encoding="utf-8") as stream:
            json.dump(result, stream, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, args.output)
        temporary = None
        return 0
    except Failure as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except (OSError, ValueError, TypeError):
        print("Error: could not read input, process the response, or write output.", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrupted; any submitted transcript remains in AssemblyAI for review.", file=sys.stderr)
        return 130
    finally:
        if temporary is not None:
            os.unlink(temporary)


if __name__ == "__main__":
    sys.exit(main())
