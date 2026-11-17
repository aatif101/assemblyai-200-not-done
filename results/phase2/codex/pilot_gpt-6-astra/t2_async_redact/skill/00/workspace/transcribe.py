"""Transcribe support calls with AssemblyAI's server-side PII redaction."""

import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
import urllib.error
import urllib.request


BASE_URL = "https://api.assemblyai.com/v2"
POLICIES = ["person_name", "phone_number", "us_social_security_number"]


class Failure(Exception):
    pass


def request(key, method, path, data=None, content_type="application/json", length=None):
    headers = {"Authorization": key, "Content-Type": content_type}
    if length is not None:
        headers["Content-Length"] = str(length)
    req = urllib.request.Request(BASE_URL + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        # Never print response bodies: they could contain sensitive text.
        raise Failure(f"AssemblyAI {method} request failed (HTTP {exc.code}).") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise Failure("AssemblyAI connection failed or timed out.") from None
    except (ValueError, UnicodeError):
        raise Failure("AssemblyAI returned invalid JSON.") from None
    if not isinstance(result, dict):
        raise Failure("AssemblyAI returned an unexpected response.")
    return result


def output_record(result, transcript_id):
    # Fail closed if the service does not confirm the redaction configuration.
    if (result.get("redact_pii") is not True
            or not set(POLICIES).issubset(result.get("redact_pii_policies") or [])
            or result.get("redact_pii_return_unredacted") is True
            or any(result.get(k) for k in ("unredacted_text", "unredacted_words", "unredacted_utterances"))):
        raise Failure("AssemblyAI did not confirm the required redaction; no output written.")
    text = result.get("text")
    utterances = result.get("utterances")
    if not isinstance(text, str) or not isinstance(utterances, list):
        raise Failure("AssemblyAI returned missing text or utterances.")
    clean = []
    for utterance in utterances:
        if (not isinstance(utterance, dict)
                or not isinstance(utterance.get("speaker"), str)
                or not isinstance(utterance.get("text"), str)):
            raise Failure("AssemblyAI returned an invalid utterance.")
        clean.append({"speaker": utterance["speaker"], "text": utterance["text"]})
    return {"transcript_id": transcript_id, "text": text, "utterances": clean}


def main():
    if len(sys.argv) != 3:
        raise Failure("Usage: ./run.sh <audio.wav> <out.json>")
    key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
    if not key:
        raise Failure("ASSEMBLYAI_API_KEY is not set.")
    source, destination = map(Path, sys.argv[1:])
    if source.resolve() == destination.resolve():
        raise Failure("Input and output must be different files.")
    if not destination.parent.is_dir():
        raise Failure("Output directory does not exist.")
    with source.open("rb") as audio:
        size = os.fstat(audio.fileno()).st_size
        if not size:
            raise Failure("Audio file is empty.")
        upload = request(key, "POST", "/upload", audio, "application/octet-stream", size)
    if not isinstance(upload.get("upload_url"), str):
        raise Failure("AssemblyAI did not return an upload URL.")
    config = {
        "audio_url": upload["upload_url"],
        "speech_models": ["universal-3-5-pro"],
        "speaker_labels": True,
        "speaker_options": {"min_speakers_expected": 2, "max_speakers_expected": 2},
        "redact_pii": True,
        "redact_pii_policies": POLICIES,
        "redact_pii_sub": "entity_name",
        "redact_pii_return_unredacted": False,
    }
    result = request(key, "POST", "/transcript", json.dumps(config).encode())
    transcript_id = result.get("id")
    if not isinstance(transcript_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", transcript_id):
        raise Failure("AssemblyAI did not return a valid transcript ID.")
    print(f"AssemblyAI transcript ID: {transcript_id}", file=sys.stderr)
    deadline = time.monotonic() + 6 * 60 * 60
    while True:
        status = result.get("status")
        if status == "completed":
            break
        if status == "error":
            raise Failure("AssemblyAI transcription failed; no output written.")
        if status not in ("queued", "processing"):
            raise Failure("AssemblyAI returned an unexpected status.")
        if time.monotonic() >= deadline:
            raise Failure("Transcription polling timed out; transcript retained in AssemblyAI.")
        time.sleep(3)
        result = request(key, "GET", "/transcript/" + transcript_id)
    record = output_record(result, transcript_id)
    # Only the final, redacted projection reaches disk. Atomic replacement keeps
    # an interrupted/failed run from producing a partial success-looking file.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=destination.parent,
                                         prefix=".transcript-", delete=False) as output:
            temporary = output.name
            json.dump(record, output, ensure_ascii=False)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


if __name__ == "__main__":
    try:
        main()
    except Failure as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    except OSError:
        print("Error: unable to read audio or write output.", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("Interrupted; any submitted transcript remains in AssemblyAI.", file=sys.stderr)
        sys.exit(130)
