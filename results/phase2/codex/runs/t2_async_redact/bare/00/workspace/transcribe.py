#!/usr/bin/env python3
"""Submit a call with server-side PII redaction and speaker diarization."""

import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
import urllib.error
import urllib.request


API = "https://api.assemblyai.com/v2"
POLICIES = ["person_name", "phone_number", "us_social_security_number"]
POLL_SECONDS = 3
MAX_WAIT_SECONDS = 7200


class TranscriptionError(Exception):
    pass


def request(key, path, payload=None, audio=None):
    headers = {"authorization": key}
    if audio is not None:
        headers["content-type"] = "application/octet-stream"
        data = audio
    elif payload is not None:
        headers["content-type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")
    else:
        data = None
    req = urllib.request.Request(API + path, data=data, headers=headers,
                                 method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        # Response bodies can contain transcript content. Never log them.
        raise TranscriptionError(f"AssemblyAI request failed (HTTP {exc.code}).") from None
    except (OSError, ValueError) as exc:
        raise TranscriptionError("AssemblyAI connection failed or returned invalid JSON.") from None
    if not isinstance(result, dict):
        raise TranscriptionError("AssemblyAI returned an unexpected response.")
    return result


def transcribe(key, audio_path):
    with audio_path.open("rb") as audio:
        uploaded = request(key, "/upload", audio=audio)
    url = uploaded.get("upload_url")
    if not isinstance(url, str) or not url.startswith("https://"):
        raise TranscriptionError("AssemblyAI did not return an upload URL.")
    job = request(key, "/transcript", payload={
        "audio_url": url,
        "speech_models": ["universal-2"],
        "speaker_labels": True,
        "speakers_expected": 2,
        "redact_pii": True,
        "redact_pii_policies": POLICIES,
        "redact_pii_sub": "entity_name",
        "redact_pii_return_unredacted": False,
    })
    transcript_id = job.get("id")
    if not isinstance(transcript_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", transcript_id):
        raise TranscriptionError("AssemblyAI did not return a valid transcript ID.")
    # An ID lets operators find incomplete jobs without exposing transcript text.
    print(f"AssemblyAI transcript_id: {transcript_id}", file=sys.stderr)
    deadline = time.monotonic() + MAX_WAIT_SECONDS
    while True:
        status = job.get("status")
        if status == "completed":
            break
        if status == "error":
            raise TranscriptionError("AssemblyAI transcription failed; review the job in AssemblyAI.")
        if status not in ("queued", "processing"):
            raise TranscriptionError("AssemblyAI returned an unexpected job status.")
        if time.monotonic() >= deadline:
            raise TranscriptionError("Timed out waiting for AssemblyAI; the job remains available for review.")
        time.sleep(POLL_SECONDS)
        job = request(key, f"/transcript/{transcript_id}")

    # Fail closed if the service does not confirm the requested privacy settings.
    if (job.get("id") != transcript_id
            or job.get("redact_pii") is not True
            or not set(POLICIES).issubset(job.get("redact_pii_policies") or [])
            or job.get("redact_pii_return_unredacted") is True
            or any(job.get(field) is not None for field in
                   ("unredacted_text", "unredacted_words", "unredacted_utterances"))):
        raise TranscriptionError("AssemblyAI did not confirm the required redaction settings.")
    text = job.get("text")
    utterances = job.get("utterances")
    if not isinstance(text, str) or not isinstance(utterances, list):
        raise TranscriptionError("AssemblyAI returned missing text or speaker utterances.")
    cleaned = []
    for item in utterances:
        if (not isinstance(item, dict) or not isinstance(item.get("speaker"), str)
                or not isinstance(item.get("text"), str)):
            raise TranscriptionError("AssemblyAI returned an invalid utterance.")
        cleaned.append({"speaker": item["speaker"], "text": item["text"]})
    return {"transcript_id": transcript_id, "text": text, "utterances": cleaned}


def write_output(path, result):
    # Only the final, redacted, allowlisted fields reach disk. Atomic replacement
    # also keeps a failed run from leaving a partial JSON document.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".transcript-", delete=False) as output:
            temporary = output.name
            json.dump(result, output, ensure_ascii=False)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def main():
    if len(sys.argv) != 3:
        print("Usage: ./run.sh <audio.wav> <out.json>", file=sys.stderr)
        return 2
    key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
    if not key:
        print("Error: ASSEMBLYAI_API_KEY is not set.", file=sys.stderr)
        return 1
    audio_path, output_path = map(Path, sys.argv[1:])
    try:
        if audio_path.resolve() == output_path.resolve():
            raise TranscriptionError("Audio and output paths must be different.")
        if not audio_path.is_file() or audio_path.stat().st_size == 0:
            raise TranscriptionError("Input audio must be a nonempty file.")
        if not output_path.parent.is_dir():
            raise TranscriptionError("Output directory does not exist.")
        result = transcribe(key, audio_path)
        write_output(output_path, result)
    except (TranscriptionError, OSError) as exc:
        # Filesystem messages may contain user-supplied sensitive filenames.
        message = str(exc) if isinstance(exc, TranscriptionError) else "Could not read input or write output."
        print(f"Error: {message}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrupted; submitted jobs remain in AssemblyAI for review.", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
