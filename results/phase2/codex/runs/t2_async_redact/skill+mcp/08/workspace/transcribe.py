#!/usr/bin/env python3
"""Transcribe support calls with server-side PII redaction. No deletion calls."""

import json
import os
from pathlib import Path
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


BASE_URL = "https://api.assemblyai.com/v2"
POLICIES = ["person_name", "phone_number", "us_social_security_number"]


class Failure(Exception):
    pass


def request(api_key, method, path, data=None, content_type="application/json", length=None):
    headers = {"Authorization": api_key, "Content-Type": content_type}
    if length is not None:
        headers["Content-Length"] = str(length)
    # Retry reads only: a submission retry could create a duplicate transcript.
    for attempt in range(4 if method == "GET" else 1):
        try:
            req = Request(BASE_URL + path, data=data, headers=headers, method=method)
            with urlopen(req, timeout=120) as response:
                result = json.load(response)
            if not isinstance(result, dict):
                raise Failure("AssemblyAI returned an invalid response.")
            return result
        except HTTPError as exc:
            code = exc.code
            exc.close()
            if method == "GET" and (code == 429 or code >= 500) and attempt < 3:
                time.sleep(2 ** (attempt + 1))
                continue
            # Do not log response bodies: they can contain sensitive content.
            raise Failure(f"AssemblyAI {method} request failed (HTTP {code}).") from None
        except (URLError, TimeoutError, OSError):
            if method == "GET" and attempt < 3:
                time.sleep(2 ** (attempt + 1))
                continue
            raise Failure("AssemblyAI connection failed or timed out.") from None
        except (ValueError, UnicodeError):
            raise Failure("AssemblyAI returned invalid JSON.") from None


def transcribe(api_key, source):
    with source.open("rb") as audio:
        size = os.fstat(audio.fileno()).st_size
        if size == 0:
            raise Failure("The audio file is empty.")
        upload = request(api_key, "POST", "/upload", audio,
                         "application/octet-stream", size)
    upload_url = upload.get("upload_url")
    if not isinstance(upload_url, str) or not upload_url:
        raise Failure("AssemblyAI did not return an upload URL.")

    config = {
        "audio_url": upload_url,
        "speech_models": ["universal-3-5-pro", "universal-2"],
        "language_code": "en_us",
        "speaker_labels": True,
        "speaker_options": {"min_speakers_expected": 2, "max_speakers_expected": 2},
        "format_text": True,
        "redact_pii": True,
        "redact_pii_policies": POLICIES,
        "redact_pii_sub": "entity_name",
        "redact_pii_return_unredacted": False,
        "speech_understanding": {
            "request": {
                "speaker_identification": {
                    "speaker_type": "role",
                    "known_values": ["Agent", "Customer"],
                }
            }
        },
    }
    result = request(api_key, "POST", "/transcript", json.dumps(config).encode())
    transcript_id = result.get("id")
    if not isinstance(transcript_id, str) or not transcript_id:
        raise Failure("AssemblyAI did not return a transcript ID.")
    print(f"Transcript ID: {transcript_id}", file=sys.stderr)
    deadline = time.monotonic() + 6 * 60 * 60
    while True:
        status = result.get("status")
        if status == "completed":
            break
        if status == "error":
            raise Failure("AssemblyAI transcription failed; review the transcript ID in AssemblyAI.")
        if status not in ("queued", "processing"):
            raise Failure("AssemblyAI returned an unexpected transcript status.")
        if time.monotonic() >= deadline:
            raise Failure("Timed out waiting for transcription; the remote transcript is retained.")
        time.sleep(3)
        result = request(api_key, "GET", "/transcript/" + quote(transcript_id, safe=""))

    # Fail closed if the service did not apply the required privacy settings.
    if (result.get("redact_pii") is not True
            or not set(POLICIES).issubset(result.get("redact_pii_policies") or [])
            or result.get("redact_pii_return_unredacted") is True
            or any(result.get(key) is not None for key in
                   ("unredacted_text", "unredacted_words", "unredacted_utterances"))):
        raise Failure("Could not verify server-side PII redaction; no output written.")
    text = result.get("text")
    utterances = result.get("utterances")
    if not isinstance(text, str) or not isinstance(utterances, list):
        raise Failure("AssemblyAI returned no transcript text or speaker utterances.")
    output = []
    for utterance in utterances:
        if (not isinstance(utterance, dict)
                or not isinstance(utterance.get("speaker"), str)
                or not isinstance(utterance.get("text"), str)):
            raise Failure("AssemblyAI returned an invalid speaker utterance.")
        output.append({"speaker": utterance["speaker"], "text": utterance["text"]})
    if text.strip() and not output:
        raise Failure("AssemblyAI returned speech without speaker utterances.")
    if (result.get("metadata") or {}).get("warnings"):
        print("AssemblyAI reported processing warnings; review the transcript ID.", file=sys.stderr)
    return {"transcript_id": transcript_id, "text": text, "utterances": output}


def write_output(destination, result):
    # Atomic replacement leaves no partial JSON on failure; temporary file is 0600.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=destination.parent,
                                         prefix=".transcript-", delete=False) as handle:
            temporary = handle.name
            json.dump(result, handle, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def main():
    if len(sys.argv) != 3:
        raise Failure("Usage: ./run.sh <audio.wav> <out.json>")
    api_key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
    if not api_key:
        raise Failure("Set ASSEMBLYAI_API_KEY in the environment.")
    source = Path(sys.argv[1]).resolve()
    destination = Path(sys.argv[2]).resolve()
    if source == destination:
        raise Failure("The output path must differ from the audio path.")
    if not source.is_file():
        raise Failure("The audio file does not exist or is not a regular file.")
    if not destination.parent.is_dir() or destination.is_dir():
        raise Failure("The output path must be a file in an existing directory.")
    write_output(destination, transcribe(api_key, source))


if __name__ == "__main__":
    try:
        main()
    except Failure as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    except OSError:
        print("Error: Could not read the audio or write the output file.", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("Interrupted; any submitted transcript is retained in AssemblyAI.", file=sys.stderr)
        sys.exit(130)
