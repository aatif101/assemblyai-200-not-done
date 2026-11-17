"""Submit server-redacted, two-speaker transcripts. Python standard library only."""

import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
import urllib.error
import urllib.request

BASE = "https://api.assemblyai.com/v2"
POLICIES = ["person_name", "phone_number", "us_social_security_number"]


class Failure(Exception):
    pass


def request(method, path, key, data=None, content_type="application/json", length=None):
    headers = {"Authorization": key, "Content-Type": content_type}
    if length is not None:
        headers["Content-Length"] = str(length)
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        # Server error bodies can contain sensitive content; never print them.
        raise Failure(f"AssemblyAI {method} {path}: HTTP {exc.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise Failure(f"AssemblyAI {method} {path}: network request failed") from None
    except (ValueError, UnicodeError):
        raise Failure("AssemblyAI returned invalid JSON") from None
    if not isinstance(result, dict):
        raise Failure("AssemblyAI returned an unexpected response")
    return result


def project(result, transcript_id):
    if result.get("id") != transcript_id:
        raise Failure("Transcript ID mismatch")
    if result.get("redact_pii") is not True or not set(POLICIES).issubset(
        result.get("redact_pii_policies") or []
    ):
        raise Failure("Server did not confirm the required PII redaction settings")
    if result.get("redact_pii_return_unredacted") or any(
        result.get(field) is not None
        for field in ("unredacted_text", "unredacted_words", "unredacted_utterances")
    ):
        raise Failure("Server unexpectedly returned unredacted content")
    text = result.get("text")
    utterances = result.get("utterances")
    if not isinstance(text, str) or not isinstance(utterances, list):
        raise Failure("Completed transcript is missing text or speaker utterances")
    output = []
    for utterance in utterances:
        if not isinstance(utterance, dict) or not isinstance(utterance.get("text"), str):
            raise Failure("Invalid utterance")
        speaker = utterance.get("speaker")
        if not isinstance(speaker, str) or not re.fullmatch(r"[A-Z]+", speaker):
            raise Failure("Missing or unexpected anonymous speaker label")
        output.append({"speaker": speaker, "text": utterance["text"]})
    if text.strip() and not output:
        raise Failure("Transcript has speech but no speaker utterances")
    return {"transcript_id": transcript_id, "text": text, "utterances": output}


def atomic_write(destination, result):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=destination.parent,
                                         prefix=".transcript-", delete=False) as stream:
            temporary = stream.name
            json.dump(result, stream, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def main():
    if len(sys.argv) != 3:
        raise Failure("Usage: ./run.sh <audio.wav> <out.json>")
    key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
    if not key:
        raise Failure("ASSEMBLYAI_API_KEY is not set")
    source, destination = map(Path, sys.argv[1:])
    if source.resolve() == destination.resolve():
        raise Failure("Input and output must be different files")
    if not source.is_file() or not source.stat().st_size:
        raise Failure("Input must be a nonempty audio file")
    if not destination.parent.is_dir() or destination.is_dir():
        raise Failure("Output must be a file in an existing directory")
    with source.open("rb") as audio:
        upload = request("POST", "/upload", key, audio, "application/octet-stream",
                         os.fstat(audio.fileno()).st_size)
    if not isinstance(upload.get("upload_url"), str):
        raise Failure("Upload response is missing upload_url")
    config = {
        "audio_url": upload["upload_url"],
        "speech_models": ["universal-3-5-pro"],
        "speaker_labels": True,
        "speakers_expected": 2,
        "redact_pii": True,
        "redact_pii_policies": POLICIES,
        "redact_pii_sub": "entity_name",
        "redact_pii_return_unredacted": False,
    }
    result = request("POST", "/transcript", key, json.dumps(config).encode())
    transcript_id = result.get("id")
    if not isinstance(transcript_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", transcript_id):
        raise Failure("Submission response is missing a valid transcript ID")
    print(f"AssemblyAI transcript ID: {transcript_id}", file=sys.stderr)
    deadline = time.monotonic() + 6 * 60 * 60
    while True:
        result = request("GET", f"/transcript/{transcript_id}", key)
        status = result.get("status")
        if status == "completed":
            break
        if status == "error":
            raise Failure("AssemblyAI transcription failed; review the transcript ID above")
        if status not in ("queued", "processing"):
            raise Failure("AssemblyAI returned an unexpected transcript status")
        if time.monotonic() >= deadline:
            raise Failure("Polling timed out; transcript remains available for your retention job")
        time.sleep(3)
    atomic_write(destination, project(result, transcript_id))


if __name__ == "__main__":
    try:
        main()
    except Failure as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    except (OSError, ValueError, TypeError):
        print("Error: local I/O or response validation failed", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("Interrupted; any submitted transcript remains with AssemblyAI", file=sys.stderr)
        sys.exit(130)
