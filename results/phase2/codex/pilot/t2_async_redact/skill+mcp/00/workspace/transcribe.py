#!/usr/bin/env python3
"""Upload and transcribe calls with AssemblyAI's server-side PII redaction."""

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


class Failure(Exception):
    pass


def request(key, method, path, data=None, headers=None):
    req = urllib.request.Request(
        BASE_URL + path,
        data=data,
        headers={"Authorization": key, **(headers or {})},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        # Error bodies can contain source text. Never print or persist them.
        raise Failure(f"AssemblyAI {method} request failed (HTTP {exc.code}).") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise Failure(f"AssemblyAI {method} request failed due to a connection error.") from None
    except (ValueError, UnicodeError):
        raise Failure("AssemblyAI returned invalid JSON.") from None
    if not isinstance(result, dict):
        raise Failure("AssemblyAI returned an invalid response.")
    return result


def make_output(result):
    # Fail closed if the service did not apply the requested privacy settings.
    if (result.get("redact_pii") is not True
            or not set(POLICIES).issubset(result.get("redact_pii_policies") or [])
            or result.get("redact_pii_return_unredacted") is True
            or any(result.get(field) is not None for field in
                   ("unredacted_text", "unredacted_words", "unredacted_utterances"))):
        raise Failure("AssemblyAI did not confirm the required redaction settings.")
    if result.get("speaker_labels") is not True:
        raise Failure("AssemblyAI did not confirm speaker diarization.")
    text = result.get("text")
    utterances = result.get("utterances")
    if not isinstance(text, str) or not isinstance(utterances, list):
        raise Failure("AssemblyAI returned no usable text or utterances.")
    output_utterances = []
    for utterance in utterances:
        if (not isinstance(utterance, dict)
                or not isinstance(utterance.get("speaker"), str)
                or not isinstance(utterance.get("text"), str)):
            raise Failure("AssemblyAI returned an invalid utterance.")
        output_utterances.append({"speaker": utterance["speaker"], "text": utterance["text"]})
    return {"transcript_id": result["id"], "text": text, "utterances": output_utterances}


def main():
    if len(sys.argv) != 3:
        raise Failure("Usage: ./run.sh <audio.wav> <out.json>")
    key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
    if not key:
        raise Failure("ASSEMBLYAI_API_KEY must be set.")
    audio = Path(sys.argv[1])
    output = Path(sys.argv[2])
    if audio.resolve() == output.resolve():
        raise Failure("Input audio and output JSON must be different files.")
    if not audio.is_file() or audio.stat().st_size == 0:
        raise Failure("Input audio must be a nonempty readable file.")
    if not output.parent.is_dir():
        raise Failure("Output directory does not exist.")
    with audio.open("rb") as stream:
        uploaded = request(key, "POST", "/upload", stream, {
            "Content-Type": "application/octet-stream",
            "Content-Length": str(os.fstat(stream.fileno()).st_size),
        })
    if not isinstance(uploaded.get("upload_url"), str):
        raise Failure("AssemblyAI did not return an upload URL.")
    config = {
        "audio_url": uploaded["upload_url"],
        "speech_models": ["universal-3-5-pro"],
        "speaker_labels": True,
        "speaker_options": {"min_speakers_expected": 2, "max_speakers_expected": 2},
        "redact_pii": True,
        "redact_pii_policies": POLICIES,
        "redact_pii_sub": "entity_name",
        "redact_pii_return_unredacted": False,
    }
    # Submit only once: blindly retrying POST can create duplicate retained jobs.
    result = request(key, "POST", "/transcript", json.dumps(config).encode(),
                     {"Content-Type": "application/json"})
    transcript_id = result.get("id")
    if not isinstance(transcript_id, str) or not transcript_id:
        raise Failure("AssemblyAI did not return a transcript ID.")
    print(f"AssemblyAI transcript ID: {transcript_id}", file=sys.stderr)
    endpoint = "/transcript/" + urllib.parse.quote(transcript_id, safe="")
    deadline = time.monotonic() + 3600
    while True:
        result = request(key, "GET", endpoint)
        if result.get("id") != transcript_id:
            raise Failure("AssemblyAI returned a mismatched transcript ID.")
        status = result.get("status")
        if status == "completed":
            break
        if status == "error":
            raise Failure("AssemblyAI transcription failed; review the job using its transcript ID.")
        if status not in ("queued", "processing"):
            raise Failure("AssemblyAI returned an unknown transcript status.")
        if time.monotonic() >= deadline:
            raise Failure("Timed out waiting for transcription; the job remains in AssemblyAI.")
        time.sleep(3)
    payload = make_output(result)
    # Only the requested redacted fields reach disk. Replace atomically with mode 0600.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=output.parent,
                                         prefix=".transcript-", delete=False) as dest:
            temporary = dest.name
            json.dump(payload, dest, ensure_ascii=False)
            dest.write("\n")
        os.replace(temporary, output)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)
    # Retention and deletion belong to the caller's review job. Never DELETE here.


if __name__ == "__main__":
    try:
        main()
    except Failure as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("Interrupted; any submitted AssemblyAI job remains available for review.", file=sys.stderr)
        sys.exit(130)
    except Exception:
        print("Error: unable to process the call or write the output.", file=sys.stderr)
        sys.exit(1)
