#!/usr/bin/env python3
"""Transcribe a call using server-side PII redaction; never delete remote jobs."""

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


class TranscriptionError(Exception):
    pass


def request_json(api_key, method, path, data=None, content_type="application/json",
                 content_length=None):
    headers = {"Authorization": api_key, "Content-Type": content_type}
    if content_length is not None:
        headers["Content-Length"] = str(content_length)
    request = urllib.request.Request(API + path, data=data, headers=headers, method=method)
    # Only reads are retried: a lost POST response may already have created a job.
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                result = json.load(response)
            if not isinstance(result, dict):
                raise TranscriptionError("AssemblyAI returned an invalid response.")
            return result
        except urllib.error.HTTPError as exc:
            if method == "GET" and exc.code in (429, 500, 502, 503, 504) and attempt < 3:
                time.sleep(2 ** attempt)
                continue
            # Do not print response bodies: they can contain sensitive data.
            raise TranscriptionError(f"AssemblyAI {method} failed (HTTP {exc.code}).") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            if method == "GET" and attempt < 3:
                time.sleep(2 ** attempt)
                continue
            raise TranscriptionError(f"AssemblyAI {method} failed due to a connection error.") from None
        except (ValueError, UnicodeError):
            raise TranscriptionError("AssemblyAI returned invalid JSON.") from None


def transcribe(api_key, audio):
    with audio.open("rb") as source:
        upload = request_json(api_key, "POST", "/upload", source,
                              "application/octet-stream", os.fstat(source.fileno()).st_size)
    url = upload.get("upload_url")
    if not isinstance(url, str) or not url:
        raise TranscriptionError("AssemblyAI did not return an upload URL.")
    config = {
        "audio_url": url,
        "speech_models": ["universal-3-5-pro", "universal-2"],
        "speaker_labels": True,
        "speaker_options": {"min_speakers_expected": 2, "max_speakers_expected": 2},
        "redact_pii": True,
        "redact_pii_policies": POLICIES,
        "redact_pii_sub": "entity_name",
        "redact_pii_return_unredacted": False,
    }
    job = request_json(api_key, "POST", "/transcript", json.dumps(config).encode("utf-8"))
    transcript_id = job.get("id")
    if not isinstance(transcript_id, str) or not transcript_id:
        raise TranscriptionError("AssemblyAI did not return a transcript ID.")
    print(f"AssemblyAI transcript ID: {transcript_id}", file=sys.stderr)
    deadline = time.monotonic() + 7200
    path = "/transcript/" + urllib.parse.quote(transcript_id, safe="")
    while True:
        job = request_json(api_key, "GET", path)
        status = job.get("status")
        if status == "completed":
            break
        if status == "error":
            raise TranscriptionError("AssemblyAI transcription failed; review the job by its ID.")
        if status not in ("queued", "processing"):
            raise TranscriptionError("AssemblyAI returned an unknown transcription status.")
        if time.monotonic() >= deadline:
            raise TranscriptionError("Timed out waiting for AssemblyAI; the remote job is retained.")
        time.sleep(3)

    # Fail closed if the service did not apply the requested privacy settings.
    if (job.get("redact_pii") is not True
            or job.get("redact_pii_return_unredacted") is True
            or not set(POLICIES).issubset(job.get("redact_pii_policies") or [])
            or any(job.get(k) is not None for k in
                   ("unredacted_text", "unredacted_words", "unredacted_utterances"))):
        raise TranscriptionError("AssemblyAI did not confirm the requested redaction settings.")
    text = job.get("text")
    utterances = job.get("utterances")
    if text is None and not job.get("words"):
        text = ""
        utterances = utterances or []
    if not isinstance(text, str) or not isinstance(utterances, list):
        raise TranscriptionError("AssemblyAI returned an invalid transcript or missing utterances.")
    output_utterances = []
    for utterance in utterances:
        if (not isinstance(utterance, dict)
                or not isinstance(utterance.get("speaker"), str)
                or not isinstance(utterance.get("text"), str)):
            raise TranscriptionError("AssemblyAI returned an invalid speaker utterance.")
        output_utterances.append({"speaker": utterance["speaker"], "text": utterance["text"]})
    return {"transcript_id": transcript_id, "text": text, "utterances": output_utterances}


def write_output(path, result):
    # Replace atomically so failures never leave a partial result; private permissions.
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".transcript-", delete=False) as stream:
            temp_path = stream.name
            json.dump(result, stream, ensure_ascii=False)
            stream.write("\n")
        os.replace(temp_path, path)
    finally:
        if temp_path is not None and os.path.exists(temp_path):
            os.unlink(temp_path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
    if not key:
        raise TranscriptionError("ASSEMBLYAI_API_KEY is required.")
    if not args.audio.is_file() or args.audio.stat().st_size == 0:
        raise TranscriptionError("Audio must be a nonempty file.")
    if args.audio.resolve() == args.output.resolve():
        raise TranscriptionError("Output must be different from the audio file.")
    if not args.output.parent.is_dir() or args.output.is_dir():
        raise TranscriptionError("Output must name a file in an existing directory.")
    write_output(args.output, transcribe(key, args.audio))


if __name__ == "__main__":
    try:
        main()
    except (TranscriptionError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("Interrupted; any remote transcript is retained.", file=sys.stderr)
        sys.exit(130)
