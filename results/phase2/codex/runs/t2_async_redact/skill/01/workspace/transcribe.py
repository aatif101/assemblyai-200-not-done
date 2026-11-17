#!/usr/bin/env python3
"""Upload and transcribe a call with server-side PII redaction and diarization."""

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


class CallError(Exception):
    pass


def request(api_key, method, endpoint, *, payload=None, audio=None):
    headers = {"Authorization": api_key}
    data = None
    if audio is not None:
        data = audio
        headers["Content-Type"] = "application/octet-stream"
        headers["Content-Length"] = str(os.fstat(audio.fileno()).st_size)
    elif payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        BASE_URL + endpoint, data=data, headers=headers, method=method
    )
    # Retry only reads: retrying submission could create duplicate paid jobs.
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=300) as response:
                result = json.load(response)
            if not isinstance(result, dict):
                raise CallError("AssemblyAI returned an invalid response.")
            return result
        except urllib.error.HTTPError as exc:
            code = exc.code
            exc.close()
            if method == "GET" and (code == 429 or code >= 500) and attempt < 4:
                time.sleep(2 ** attempt)
                continue
            # API response bodies may contain source text; never log them.
            raise CallError(f"AssemblyAI {method} request failed (HTTP {code}).") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            if method == "GET" and attempt < 4:
                time.sleep(2 ** attempt)
                continue
            raise CallError("AssemblyAI connection failed or timed out.") from None
        except (ValueError, UnicodeError):
            raise CallError("AssemblyAI returned invalid JSON.") from None


def transcribe(api_key, audio_path):
    with audio_path.open("rb") as audio:
        upload = request(api_key, "POST", "/upload", audio=audio)
    if not isinstance(upload.get("upload_url"), str) or not upload["upload_url"]:
        raise CallError("AssemblyAI did not return an upload URL.")
    job = request(api_key, "POST", "/transcript", payload={
        "audio_url": upload["upload_url"],
        "speech_models": ["universal-3-5-pro", "universal-2"],
        "speaker_labels": True,
        "speaker_options": {"min_speakers_expected": 2, "max_speakers_expected": 2},
        "redact_pii": True,
        "redact_pii_policies": POLICIES,
        "redact_pii_sub": "entity_name",
        "redact_pii_return_unredacted": False,
    })
    transcript_id = job.get("id")
    if not isinstance(transcript_id, str) or not transcript_id:
        raise CallError("AssemblyAI did not return a transcript ID.")
    print(f"AssemblyAI transcript_id: {transcript_id}", file=sys.stderr)
    endpoint = "/transcript/" + urllib.parse.quote(transcript_id, safe="")
    deadline = time.monotonic() + 24 * 60 * 60
    # Fetch the persisted record, even if submission already returned completed.
    while True:
        job = request(api_key, "GET", endpoint)
        if job.get("status") == "completed":
            break
        if job.get("status") == "error":
            raise CallError("AssemblyAI transcription failed; review the job using its ID.")
        if job.get("status") not in ("queued", "processing"):
            raise CallError("AssemblyAI returned an unexpected transcription status.")
        if time.monotonic() >= deadline:
            raise CallError("Timed out waiting for transcription; the remote job is retained.")
        time.sleep(3)

    # Fail closed if the persisted job does not confirm the requested protection.
    if (job.get("redact_pii") is not True
            or not set(POLICIES).issubset(job.get("redact_pii_policies") or [])
            or job.get("redact_pii_return_unredacted") is True
            or any(job.get(key) for key in
                   ("unredacted_text", "unredacted_words", "unredacted_utterances"))):
        raise CallError("The persisted transcript did not confirm PII redaction; output withheld.")
    if job.get("id") != transcript_id or not isinstance(job.get("text"), str):
        raise CallError("AssemblyAI returned an invalid transcript.")
    utterances = job.get("utterances")
    if not isinstance(utterances, list):
        raise CallError("AssemblyAI did not return speaker utterances.")
    cleaned = []
    for utterance in utterances:
        if (not isinstance(utterance, dict)
                or not isinstance(utterance.get("speaker"), str)
                or not isinstance(utterance.get("text"), str)):
            raise CallError("AssemblyAI returned an invalid speaker utterance.")
        cleaned.append({"speaker": utterance["speaker"], "text": utterance["text"]})
    return {"transcript_id": transcript_id, "text": job["text"], "utterances": cleaned}


def write_result(path, result):
    # Atomically publish only the final, redacted result. Private permissions.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".transcript-", delete=False) as handle:
            temporary = handle.name
            json.dump(result, handle, ensure_ascii=False)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary is not None and os.path.exists(temporary):
            os.unlink(temporary)


def main():
    if len(sys.argv) != 3:
        raise CallError("Usage: ./run.sh <audio.wav> <out.json>")
    api_key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
    if not api_key:
        raise CallError("Set ASSEMBLYAI_API_KEY in the environment.")
    audio_path, output_path = map(Path, sys.argv[1:])
    if not audio_path.is_file():
        raise CallError("Input audio file does not exist.")
    if audio_path.resolve() == output_path.resolve():
        raise CallError("Input and output paths must differ.")
    if not output_path.parent.is_dir() or output_path.is_dir():
        raise CallError("Output must be a file in an existing directory.")
    result = transcribe(api_key, audio_path)
    write_result(output_path, result)


if __name__ == "__main__":
    try:
        main()
    except (CallError, OSError) as exc:
        # OSError paths can be user supplied: omit details along with API bodies.
        message = str(exc) if isinstance(exc, CallError) else "Local file operation failed."
        print(f"Error: {message}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("Interrupted; any submitted AssemblyAI transcript is retained.", file=sys.stderr)
        sys.exit(130)
