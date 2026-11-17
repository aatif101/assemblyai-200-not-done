#!/usr/bin/env python3
"""Transcribe locally supplied audio with server-side PII redaction."""

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
POLL_TIMEOUT = 6 * 60 * 60


class TranscriptionError(Exception):
    pass


def request_json(api_key, method, endpoint, data=None, headers=None):
    request = urllib.request.Request(
        BASE_URL + endpoint,
        data=data,
        method=method,
        headers={"Authorization": api_key, **(headers or {})},
    )
    # Retry reads only: retrying submission could create duplicate retained jobs.
    attempts = 4 if method == "GET" else 1
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                result = json.load(response)
            if not isinstance(result, dict):
                raise TranscriptionError("AssemblyAI returned an invalid response.")
            return result
        except urllib.error.HTTPError as exc:
            exc.close()
            if exc.code not in (429, 500, 502, 503, 504) or attempt == attempts - 1:
                # Response bodies can contain sensitive data. Never log them.
                raise TranscriptionError(f"AssemblyAI {method} failed (HTTP {exc.code}).") from None
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if attempt == attempts - 1:
                raise TranscriptionError(f"AssemblyAI {method} failed: network error or timeout.") from None
        except (ValueError, UnicodeError):
            raise TranscriptionError("AssemblyAI returned invalid JSON.") from None
        time.sleep(2 ** attempt)


def submit(api_key, audio_path):
    with audio_path.open("rb") as audio:
        uploaded = request_json(
            api_key, "POST", "/upload", data=audio,
            headers={"Content-Type": "application/octet-stream",
                     "Content-Length": str(os.fstat(audio.fileno()).st_size)},
        )
    audio_url = uploaded.get("upload_url")
    if not isinstance(audio_url, str) or not audio_url:
        raise TranscriptionError("AssemblyAI upload response has no upload URL.")
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
    return request_json(api_key, "POST", "/transcript", data=json.dumps(config).encode(),
                        headers={"Content-Type": "application/json"})


def wait_for_result(api_key, transcript_id):
    deadline = time.monotonic() + POLL_TIMEOUT
    endpoint = "/transcript/" + urllib.parse.quote(transcript_id, safe="")
    while time.monotonic() < deadline:
        result = request_json(api_key, "GET", endpoint)
        status = result.get("status")
        if status == "completed":
            return result
        if status == "error":
            raise TranscriptionError("AssemblyAI transcription failed; review the retained job.")
        if status not in ("queued", "processing"):
            raise TranscriptionError("AssemblyAI returned an unexpected transcription status.")
        time.sleep(3)
    raise TranscriptionError("Timed out waiting for AssemblyAI; the job is still retained.")


def output_document(result, transcript_id):
    # Fail closed if the server does not confirm that redaction was enabled.
    if (result.get("redact_pii") is not True
            or not set(POLICIES).issubset(result.get("redact_pii_policies") or [])
            or result.get("redact_pii_return_unredacted") is True
            or any(result.get(key) for key in
                   ("unredacted_text", "unredacted_words", "unredacted_utterances"))):
        raise TranscriptionError("AssemblyAI did not confirm the required redaction settings.")
    text = result.get("text")
    utterances = result.get("utterances")
    if text is None and not result.get("words"):
        text = ""
    if not isinstance(text, str) or (text and not isinstance(utterances, list)):
        raise TranscriptionError("AssemblyAI returned an incomplete transcript or speaker labels.")
    clean_utterances = []
    for utterance in utterances or []:
        if (not isinstance(utterance, dict)
                or not isinstance(utterance.get("speaker"), str)
                or not isinstance(utterance.get("text"), str)):
            raise TranscriptionError("AssemblyAI returned invalid utterances.")
        clean_utterances.append({"speaker": utterance["speaker"], "text": utterance["text"]})
    if len({u["speaker"] for u in clean_utterances}) > 2:
        raise TranscriptionError("AssemblyAI returned more than two speakers; review the job.")
    if (result.get("metadata") or {}).get("warnings"):
        print("AssemblyAI reported processing warnings; review the retained job.", file=sys.stderr)
    return {"transcript_id": transcript_id, "text": text, "utterances": clean_utterances}


def write_output(path, document):
    # A private, atomic write prevents a partial file from appearing as success.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".transcript-", delete=False) as output:
            temporary = Path(output.name)
            json.dump(document, output, ensure_ascii=False)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main():
    transcript_id = None
    try:
        if len(sys.argv) != 3:
            raise TranscriptionError("Usage: ./run.sh <audio.wav> <out.json>")
        api_key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
        if not api_key:
            raise TranscriptionError("ASSEMBLYAI_API_KEY is not set.")
        audio_path, output_path = map(Path, sys.argv[1:])
        if not audio_path.is_file() or audio_path.stat().st_size == 0:
            raise TranscriptionError("Input audio must be a readable, nonempty file.")
        if audio_path.resolve() == output_path.resolve():
            raise TranscriptionError("Output path must differ from the input audio path.")
        if not output_path.parent.is_dir() or output_path.is_dir():
            raise TranscriptionError("Output path must be a file in an existing directory.")
        submitted = submit(api_key, audio_path)
        transcript_id = submitted.get("id")
        if not isinstance(transcript_id, str) or not transcript_id:
            raise TranscriptionError("AssemblyAI submission response has no transcript ID.")
        print(f"AssemblyAI transcript_id: {transcript_id}", file=sys.stderr)
        result = wait_for_result(api_key, transcript_id)
        write_output(output_path, output_document(result, transcript_id))
        return 0
    except (TranscriptionError, OSError) as exc:
        # OSError filenames are local paths; API error bodies never reach here.
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrupted; any submitted AssemblyAI transcript is retained.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
