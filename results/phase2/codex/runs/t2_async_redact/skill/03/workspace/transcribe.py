#!/usr/bin/env python3
"""Transcribe a call with server-side PII redaction and speaker diarization."""

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
POLICIES = ["us_social_security_number", "phone_number", "person_name"]


class TranscriptionError(Exception):
    pass


def request_json(key, method, path, *, data=None, headers=None):
    request_headers = {"Authorization": key, **(headers or {})}
    req = urllib.request.Request(
        BASE_URL + path, data=data, headers=request_headers, method=method
    )
    # Retry reads only: replaying a submission could create duplicate paid jobs.
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=120) as response:
                result = json.load(response)
            if not isinstance(result, dict):
                raise TranscriptionError("AssemblyAI returned an invalid response.")
            return result
        except urllib.error.HTTPError as exc:
            code = exc.code
            exc.close()
            if method == "GET" and (code == 429 or code >= 500) and attempt < 3:
                time.sleep(2 ** (attempt + 1))
                continue
            # Response bodies can contain sensitive content; never log them.
            raise TranscriptionError(f"AssemblyAI request failed (HTTP {code}).") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            if method == "GET" and attempt < 3:
                time.sleep(2 ** (attempt + 1))
                continue
            raise TranscriptionError("AssemblyAI connection failed or timed out.") from None
        except (ValueError, UnicodeError):
            raise TranscriptionError("AssemblyAI returned invalid JSON.") from None


def audio_chunks(source):
    while chunk := source.read(1024 * 1024):
        yield chunk


def output_payload(result, transcript_id):
    # Fail closed if the service did not apply the required privacy settings.
    if (result.get("redact_pii") is not True
            or not set(POLICIES).issubset(result.get("redact_pii_policies") or [])
            or result.get("redact_pii_return_unredacted") is True
            or result.get("speaker_labels") is not True):
        raise TranscriptionError("AssemblyAI did not confirm the required redaction and speaker settings.")
    text = result.get("text")
    utterances = result.get("utterances")
    if not isinstance(text, str) or not isinstance(utterances, list):
        raise TranscriptionError("AssemblyAI returned no usable text or utterances.")
    clean_utterances = []
    for utterance in utterances:
        if (not isinstance(utterance, dict)
                or not isinstance(utterance.get("speaker"), str)
                or not isinstance(utterance.get("text"), str)):
            raise TranscriptionError("AssemblyAI returned an invalid utterance.")
        clean_utterances.append({"speaker": utterance["speaker"], "text": utterance["text"]})
    return {"transcript_id": transcript_id, "text": text, "utterances": clean_utterances}


def atomic_write(destination, payload):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=destination.parent,
                                         prefix=".transcript-", delete=False) as output:
            temporary = output.name
            json.dump(payload, output, ensure_ascii=False)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def main():
    if len(sys.argv) != 3:
        raise TranscriptionError("Usage: ./run.sh <audio.wav> <out.json>")
    key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
    if not key:
        raise TranscriptionError("ASSEMBLYAI_API_KEY is required.")
    audio, destination = map(Path, sys.argv[1:])
    if audio.resolve() == destination.resolve():
        raise TranscriptionError("Input and output must be different files.")
    if not audio.is_file() or not audio.stat().st_size:
        raise TranscriptionError("Input audio must be a nonempty file.")
    if not destination.parent.is_dir():
        raise TranscriptionError("Output directory does not exist.")

    with audio.open("rb") as source:
        uploaded = request_json(key, "POST", "/upload", data=audio_chunks(source), headers={
            "Content-Type": "application/octet-stream",
            "Content-Length": str(os.fstat(source.fileno()).st_size),
        })
    audio_url = uploaded.get("upload_url")
    if not isinstance(audio_url, str) or not audio_url.startswith("https://"):
        raise TranscriptionError("AssemblyAI returned no upload URL.")
    config = {
        "audio_url": audio_url,
        "speech_models": ["universal-3-5-pro", "universal-2"],
        "speaker_labels": True,
        "speaker_options": {"min_speakers_expected": 2, "max_speakers_expected": 2},
        "redact_pii": True,
        "redact_pii_policies": POLICIES,
        "redact_pii_sub": "entity_name",
        "redact_pii_return_unredacted": False,
    }
    result = request_json(key, "POST", "/transcript", data=json.dumps(config).encode(),
                          headers={"Content-Type": "application/json"})
    transcript_id = result.get("id")
    if not isinstance(transcript_id, str) or not transcript_id:
        raise TranscriptionError("AssemblyAI returned no transcript ID.")
    print(f"AssemblyAI transcript_id: {transcript_id}", file=sys.stderr)
    deadline = time.monotonic() + 24 * 60 * 60
    while result.get("status") != "completed":
        status = result.get("status")
        if status == "error":
            raise TranscriptionError("AssemblyAI transcription failed; review the job using its transcript ID.")
        if status not in ("queued", "processing"):
            raise TranscriptionError("AssemblyAI returned an unexpected job status.")
        if time.monotonic() >= deadline:
            raise TranscriptionError("Timed out waiting for AssemblyAI; the job remains available for review.")
        time.sleep(3)
        result = request_json(key, "GET", "/transcript/" + urllib.parse.quote(transcript_id, safe=""))
    atomic_write(destination, output_payload(result, transcript_id))


if __name__ == "__main__":
    try:
        main()
    except (TranscriptionError, OSError) as exc:
        # OS error messages may contain filenames; avoid echoing arbitrary paths.
        message = str(exc) if isinstance(exc, TranscriptionError) else "Local file operation failed."
        print(f"Error: {message}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("Interrupted; any submitted AssemblyAI job is retained for review.", file=sys.stderr)
        sys.exit(130)
