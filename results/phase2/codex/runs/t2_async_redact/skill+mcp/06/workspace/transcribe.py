#!/usr/bin/env python3
"""Transcribe a support call with server-side PII redaction and diarization."""

import argparse
import http.client
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time


HOST = "api.assemblyai.com"
POLICIES = ["person_name", "phone_number", "us_social_security_number"]


class TranscriptionError(Exception):
    pass


def request(method, path, key, body=None, content_type="application/json", length=None):
    headers = {"Authorization": key, "Content-Type": content_type}
    if length is not None:
        headers["Content-Length"] = str(length)
    connection = http.client.HTTPSConnection(HOST, timeout=120)
    try:
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        if not 200 <= response.status < 300:
            # Error bodies may echo sensitive data. Never print them.
            raise TranscriptionError(f"AssemblyAI {method} {path}: HTTP {response.status}")
        result = json.loads(response.read())
        if not isinstance(result, dict):
            raise TranscriptionError("AssemblyAI returned an invalid response.")
        return result
    finally:
        connection.close()


def output_record(transcript):
    # Check the server's applied settings before persisting any content.
    if (transcript.get("redact_pii") is not True
            or not set(POLICIES).issubset(transcript.get("redact_pii_policies") or [])
            or transcript.get("redact_pii_return_unredacted") not in (None, False)
            or any(k.startswith("unredacted_") for k in transcript)
            or transcript.get("speaker_labels") is not True):
        raise TranscriptionError("The response did not confirm the required privacy/speaker settings.")
    text = transcript.get("text")
    utterances = transcript.get("utterances")
    if not isinstance(text, str) or not isinstance(utterances, list):
        raise TranscriptionError("The completed transcript is missing text or utterances.")
    clean_utterances = []
    for utterance in utterances:
        if (not isinstance(utterance, dict)
                or not isinstance(utterance.get("speaker"), str)
                or not utterance["speaker"]
                or not isinstance(utterance.get("text"), str)):
            raise TranscriptionError("The completed transcript contains an invalid utterance.")
        clean_utterances.append({"speaker": utterance["speaker"], "text": utterance["text"]})
    return {"transcript_id": transcript["id"], "text": text, "utterances": clean_utterances}


def write_atomic(output, record):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=output.parent,
                                         prefix=".transcript-", delete=False) as stream:
            temporary = stream.name
            json.dump(record, stream, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, output)
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
        raise TranscriptionError("ASSEMBLYAI_API_KEY is not set.")
    if args.audio.resolve() == args.output.resolve():
        raise TranscriptionError("Input and output paths must differ.")
    if not args.output.parent.is_dir() or args.output.is_dir():
        raise TranscriptionError("Output must be a file in an existing directory.")

    with args.audio.open("rb") as audio:
        size = os.fstat(audio.fileno()).st_size
        if size == 0:
            raise TranscriptionError("Audio file is empty.")
        upload = request("POST", "/v2/upload", key, audio, "application/octet-stream", size)
    if not isinstance(upload.get("upload_url"), str) or not upload["upload_url"]:
        raise TranscriptionError("AssemblyAI did not return an upload URL.")
    config = {
        "audio_url": upload["upload_url"],
        "speech_models": ["universal-3-5-pro", "universal-2"],
        "language_code": "en_us",
        "speaker_labels": True,
        "speaker_options": {"min_speakers_expected": 2, "max_speakers_expected": 2},
        "redact_pii": True,
        "redact_pii_policies": POLICIES,
        "redact_pii_sub": "entity_name",
        "redact_pii_return_unredacted": False,
    }
    transcript = request("POST", "/v2/transcript", key, json.dumps(config).encode("utf-8"))
    transcript_id = transcript.get("id")
    if not isinstance(transcript_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", transcript_id):
        raise TranscriptionError("AssemblyAI did not return a valid transcript ID.")
    print(f"AssemblyAI transcript ID: {transcript_id}", file=sys.stderr)
    deadline = time.monotonic() + 3600
    while True:
        transcript = request("GET", f"/v2/transcript/{transcript_id}", key)
        if transcript.get("id") != transcript_id:
            raise TranscriptionError("AssemblyAI returned a mismatched transcript ID.")
        status = transcript.get("status")
        if status == "completed":
            write_atomic(args.output, output_record(transcript))
            return
        if status == "error":
            raise TranscriptionError("AssemblyAI transcription failed; review the transcript ID in AssemblyAI.")
        if status not in ("queued", "processing"):
            raise TranscriptionError("AssemblyAI returned an unexpected transcript status.")
        if time.monotonic() >= deadline:
            raise TranscriptionError("Timed out waiting for transcription; the transcript remains in AssemblyAI.")
        time.sleep(3)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Transcription interrupted; any submitted transcript remains in AssemblyAI.", file=sys.stderr)
        sys.exit(130)
    except TranscriptionError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    except (OSError, ValueError, http.client.HTTPException):
        print("Error: file, network, or API response failure. No new output was saved.", file=sys.stderr)
        sys.exit(1)
