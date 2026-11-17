#!/usr/bin/env python3
"""Transcribe support calls with AssemblyAI's server-side PII redaction."""

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


BASE_URL = "https://api.assemblyai.com/v2"
POLICIES = ["person_name", "us_social_security_number", "phone_number"]


class TranscriptionError(Exception):
    pass


def request(api_key, method, path, data=None, content_type=None, length=None):
    headers = {"Authorization": api_key}
    if content_type:
        headers["Content-Type"] = content_type
    if length is not None:
        headers["Content-Length"] = str(length)
    req = urllib.request.Request(BASE_URL + path, data=data, headers=headers, method=method)
    # Do not retry POST: a response lost after submission could create duplicate jobs.
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=120) as response:
                result = json.load(response)
            if not isinstance(result, dict):
                raise TranscriptionError("AssemblyAI returned an invalid response")
            return result
        except urllib.error.HTTPError as exc:
            if method == "GET" and exc.code in (429, 500, 502, 503, 504) and attempt < 3:
                exc.close()
                time.sleep(2 ** (attempt + 1))
                continue
            # Avoid printing API bodies, which could contain sensitive data.
            raise TranscriptionError(f"AssemblyAI {method} request failed (HTTP {exc.code})") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            if method == "GET" and attempt < 3:
                time.sleep(2 ** (attempt + 1))
                continue
            raise TranscriptionError(f"AssemblyAI {method} request failed (network error)") from None
        except (ValueError, UnicodeError):
            raise TranscriptionError("AssemblyAI returned invalid JSON") from None


def transcribe(api_key, audio):
    with audio.open("rb") as source:
        uploaded = request(api_key, "POST", "/upload", source,
                           "application/octet-stream", os.fstat(source.fileno()).st_size)
    upload_url = uploaded.get("upload_url")
    if not isinstance(upload_url, str) or not upload_url:
        raise TranscriptionError("AssemblyAI did not return an upload URL")

    config = {
        "audio_url": upload_url,
        "speech_models": ["universal-3-5-pro"],
        "language_code": "en",
        "speaker_labels": True,
        "speakers_expected": 2,
        # Redact at submission so the saved remote transcript is redacted too.
        "redact_pii": True,
        "redact_pii_policies": POLICIES,
        "redact_pii_sub": "entity_name",
        "redact_pii_return_unredacted": False,
    }
    submitted = request(api_key, "POST", "/transcript",
                        json.dumps(config).encode("utf-8"), "application/json")
    transcript_id = submitted.get("id")
    if not isinstance(transcript_id, str) or not transcript_id:
        raise TranscriptionError("AssemblyAI did not return a transcript ID")
    print(f"AssemblyAI transcript ID: {transcript_id}", file=sys.stderr)
    endpoint = "/transcript/" + urllib.parse.quote(transcript_id, safe="")
    deadline = time.monotonic() + 6 * 60 * 60
    while time.monotonic() < deadline:
        result = request(api_key, "GET", endpoint)
        status = result.get("status")
        if status == "completed":
            return make_output(transcript_id, result)
        if status == "error":
            raise TranscriptionError("AssemblyAI transcription failed; review the transcript ID in your account")
        if status not in ("queued", "processing"):
            raise TranscriptionError("AssemblyAI returned an unexpected transcript status")
        time.sleep(3)
    raise TranscriptionError("Timed out waiting for AssemblyAI; the transcript remains available for your retention job")


def make_output(transcript_id, result):
    if result.get("redact_pii") is not True:
        raise TranscriptionError("AssemblyAI did not confirm PII redaction")
    if not set(POLICIES).issubset(result.get("redact_pii_policies") or []):
        raise TranscriptionError("AssemblyAI did not confirm all required redaction policies")
    if result.get("redact_pii_return_unredacted") or any(
        result.get(field) is not None
        for field in ("unredacted_text", "unredacted_words", "unredacted_utterances")
    ):
        raise TranscriptionError("AssemblyAI unexpectedly returned unredacted data")
    text = result.get("text")
    utterances = result.get("utterances")
    if not isinstance(text, str) or not isinstance(utterances, list):
        raise TranscriptionError("AssemblyAI did not return text and speaker utterances")
    cleaned = []
    for utterance in utterances:
        if not isinstance(utterance, dict) or not isinstance(utterance.get("speaker"), str) or not isinstance(utterance.get("text"), str):
            raise TranscriptionError("AssemblyAI returned an invalid speaker utterance")
        cleaned.append({"speaker": utterance["speaker"], "text": utterance["text"]})
    if text.strip() and not cleaned:
        raise TranscriptionError("AssemblyAI returned speech without speaker utterances")
    return {"transcript_id": transcript_id, "text": text, "utterances": cleaned}


def write_output(output, result):
    # Publish only a complete successful result, with owner-only permissions.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=output.parent,
                                         prefix=".transcript-", suffix=".tmp", delete=False) as target:
            temporary = Path(target.name)
            json.dump(result, target, ensure_ascii=False)
            target.write("\n")
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    try:
        api_key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
        if not api_key:
            raise TranscriptionError("ASSEMBLYAI_API_KEY must be set")
        if not args.audio.is_file() or args.audio.stat().st_size == 0:
            raise TranscriptionError("Audio must be a nonempty readable file")
        if args.audio.resolve() == args.output.resolve():
            raise TranscriptionError("Output must differ from the audio input")
        if not args.output.parent.is_dir() or args.output.is_dir():
            raise TranscriptionError("Output must name a file in an existing directory")
        result = transcribe(api_key, args.audio)
        write_output(args.output, result)
        return 0
    except (TranscriptionError, OSError) as exc:
        # File errors omit paths, which may contain customer identifiers.
        message = str(exc) if isinstance(exc, TranscriptionError) else "File operation failed"
        print(f"Error: {message}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrupted; any submitted transcript remains in AssemblyAI", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
