#!/usr/bin/env python3
"""Transcribe calls with server-side PII redaction and speaker diarization."""

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
PII_POLICIES = ["person_name", "phone_number", "us_social_security_number"]


class TranscriptionError(Exception):
    pass


def request_json(api_key, method, path, data=None, content_type="application/json",
                 content_length=None):
    headers = {"Authorization": api_key, "Content-Type": content_type}
    if content_length is not None:
        headers["Content-Length"] = str(content_length)
    # Only retry reads: retrying a submission could create another retained job.
    attempts = 4 if method == "GET" else 1
    for attempt in range(attempts):
        request = urllib.request.Request(
            BASE_URL + path, data=data, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                result = json.load(response)
            if not isinstance(result, dict):
                raise TranscriptionError("AssemblyAI returned an invalid response.")
            return result
        except urllib.error.HTTPError as exc:
            # Do not print response bodies: they can contain sensitive data.
            if method == "GET" and exc.code in (429, 500, 502, 503, 504) and attempt + 1 < attempts:
                time.sleep(2 ** (attempt + 1))
                continue
            raise TranscriptionError(
                f"AssemblyAI {method} request failed (HTTP {exc.code})."
            ) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            if attempt + 1 < attempts:
                time.sleep(2 ** (attempt + 1))
                continue
            raise TranscriptionError(f"AssemblyAI {method} request failed (connection/timeout).") from None
        except (ValueError, UnicodeError):
            raise TranscriptionError("AssemblyAI returned invalid JSON.") from None


def transcribe(api_key, audio):
    with audio.open("rb") as stream:
        upload = request_json(
            api_key, "POST", "/upload", stream, "application/octet-stream",
            os.fstat(stream.fileno()).st_size,
        )
    if not isinstance(upload.get("upload_url"), str) or not upload["upload_url"]:
        raise TranscriptionError("AssemblyAI did not return an upload URL.")

    config = {
        "audio_url": upload["upload_url"],
        "speech_models": ["universal-3-5-pro", "universal-2"],
        "speaker_labels": True,
        "speaker_options": {"min_speakers_expected": 2, "max_speakers_expected": 2},
        "redact_pii": True,
        "redact_pii_policies": PII_POLICIES,
        "redact_pii_sub": "entity_name",
        # Keep only the redacted transcript at AssemblyAI; never request originals.
        "redact_pii_return_unredacted": False,
    }
    result = request_json(api_key, "POST", "/transcript", json.dumps(config).encode())
    transcript_id = result.get("id")
    if not isinstance(transcript_id, str) or not transcript_id:
        raise TranscriptionError("AssemblyAI did not return a transcript ID.")
    print(f"AssemblyAI transcript_id: {transcript_id}", file=sys.stderr)
    deadline = time.monotonic() + 3600
    while result.get("status") != "completed":
        if result.get("status") == "error":
            raise TranscriptionError("AssemblyAI transcription failed; review the job by its ID.")
        if result.get("status") not in ("queued", "processing"):
            raise TranscriptionError("AssemblyAI returned an unexpected job status.")
        if time.monotonic() >= deadline:
            raise TranscriptionError("Timed out waiting for AssemblyAI; the job remains available for review.")
        time.sleep(3)
        result = request_json(api_key, "GET", "/transcript/" + urllib.parse.quote(transcript_id, safe=""))

    if result.get("redact_pii") is not True:
        raise TranscriptionError("AssemblyAI did not confirm PII redaction; refusing to save output.")
    if not set(PII_POLICIES).issubset(result.get("redact_pii_policies") or []):
        raise TranscriptionError("AssemblyAI did not confirm all required redaction policies.")
    if result.get("redact_pii_return_unredacted") is True or any(
        result.get(field) for field in ("unredacted_text", "unredacted_words", "unredacted_utterances")
    ):
        raise TranscriptionError("AssemblyAI returned unredacted data; refusing to save output.")
    text = result.get("text")
    utterances = result.get("utterances")
    # A completed silent recording may have null text and utterances.
    if text is None and not utterances:
        text, utterances = "", []
    if not isinstance(text, str) or not isinstance(utterances, list):
        raise TranscriptionError("AssemblyAI returned invalid text or missing speaker utterances.")
    cleaned = []
    for utterance in utterances:
        if not isinstance(utterance, dict) or not isinstance(utterance.get("speaker"), str) or not isinstance(utterance.get("text"), str):
            raise TranscriptionError("AssemblyAI returned an invalid speaker utterance.")
        cleaned.append({"speaker": utterance["speaker"], "text": utterance["text"]})
    return {"transcript_id": transcript_id, "text": text, "utterances": cleaned}


def write_output(output, result):
    # Private temporary file and atomic replace prevent partial successful outputs.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=output.parent,
                                         prefix=".transcript-", suffix=".tmp", delete=False) as stream:
            temporary = stream.name
            json.dump(result, stream, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, output)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    try:
        api_key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
        if not api_key:
            raise TranscriptionError("ASSEMBLYAI_API_KEY is required.")
        if not args.audio.is_file() or args.audio.stat().st_size == 0:
            raise TranscriptionError("Audio must be a nonempty file.")
        if args.audio.resolve() == args.output.resolve():
            raise TranscriptionError("Audio and output must be different files.")
        if not args.output.parent.is_dir():
            raise TranscriptionError("The output directory does not exist.")
        result = transcribe(api_key, args.audio)
        write_output(args.output, result)
        return 0
    except (TranscriptionError, OSError) as exc:
        # OSError details can contain file paths; omit them from logs.
        message = str(exc) if isinstance(exc, TranscriptionError) else "Unable to read audio or write output."
        print(f"Error: {message}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrupted; any submitted AssemblyAI job remains available for review.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
