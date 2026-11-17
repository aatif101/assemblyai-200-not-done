"""Upload and transcribe calls with server-side PII redaction. No dependencies."""

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
POLICIES = ["person_name", "phone_number", "us_social_security_number"]


class TranscriptionError(Exception):
    pass


def request(api_key, method, path, data=None, content_type="application/json"):
    headers = {"Authorization": api_key, "Content-Type": content_type}
    req = urllib.request.Request(BASE_URL + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=180) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        # Server errors can echo sensitive content; never log response bodies.
        raise TranscriptionError(f"AssemblyAI {method} request failed (HTTP {exc.code}).") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise TranscriptionError(f"AssemblyAI {method} request failed; check connectivity.") from None
    except (ValueError, UnicodeError):
        raise TranscriptionError("AssemblyAI returned an invalid JSON response.") from None
    if not isinstance(result, dict):
        raise TranscriptionError("AssemblyAI returned an unexpected response.")
    return result


def transcribe(api_key, audio):
    with audio.open("rb") as source:
        size = os.fstat(source.fileno()).st_size
        if size == 0:
            raise TranscriptionError("Audio file is empty.")
        # Stream the raw file bytes, without loading a long call into memory.
        upload = request(api_key, "POST", "/upload", source, "application/octet-stream")
    upload_url = upload.get("upload_url")
    if not isinstance(upload_url, str) or not upload_url:
        raise TranscriptionError("AssemblyAI did not return an upload URL.")
    config = {
        "audio_url": upload_url,
        "speech_models": ["universal-3-5-pro", "universal-2"],
        "speaker_labels": True,
        "speaker_options": {"min_speakers_expected": 2, "max_speakers_expected": 2},
        "redact_pii": True,
        "redact_pii_policies": POLICIES,
        "redact_pii_sub": "entity_name",
        # Applies to AssemblyAI's retained transcript, not just our export.
        "redact_pii_return_unredacted": False,
    }
    result = request(api_key, "POST", "/transcript", json.dumps(config).encode())
    transcript_id = result.get("id")
    if not isinstance(transcript_id, str) or not transcript_id:
        raise TranscriptionError("AssemblyAI did not return a transcript ID.")
    # Keep the ID available for review/retention even if polling fails.
    print(f"AssemblyAI transcript ID: {transcript_id}", file=sys.stderr)
    deadline = time.monotonic() + 3600
    path = "/transcript/" + urllib.parse.quote(transcript_id, safe="")
    while result.get("status") != "completed":
        status = result.get("status")
        if status == "error":
            raise TranscriptionError("AssemblyAI transcription failed; review it using its transcript ID.")
        if status not in ("queued", "processing"):
            raise TranscriptionError("AssemblyAI returned an unexpected transcription status.")
        if time.monotonic() >= deadline:
            raise TranscriptionError("Transcription polling timed out; transcript remains available for review.")
        time.sleep(3)
        result = request(api_key, "GET", path)

    if result.get("redact_pii") is not True or not set(POLICIES).issubset(
        result.get("redact_pii_policies") or []
    ):
        raise TranscriptionError("AssemblyAI did not confirm the required PII redaction settings.")
    if result.get("redact_pii_return_unredacted") or any(
        result.get(field) for field in ("unredacted_text", "unredacted_words", "unredacted_utterances")
    ):
        raise TranscriptionError("AssemblyAI unexpectedly returned unredacted data; export refused.")
    text = result.get("text")
    utterances = result.get("utterances")
    if not isinstance(text, str) or not isinstance(utterances, list):
        raise TranscriptionError("AssemblyAI did not return transcript text and speaker utterances.")
    exported = []
    for utterance in utterances:
        if not isinstance(utterance, dict) or not isinstance(utterance.get("speaker"), str) or not isinstance(utterance.get("text"), str):
            raise TranscriptionError("AssemblyAI returned an invalid speaker utterance.")
        exported.append({"speaker": utterance["speaker"], "text": utterance["text"]})
    return {"transcript_id": transcript_id, "text": text, "utterances": exported}


def write_output(destination, result):
    # Atomic replacement: failures cannot leave a partial transcript at the destination.
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=destination.parent,
                                         prefix=".transcript-", suffix=".tmp", delete=False) as output:
            temp_path = Path(output.name)
            json.dump(result, output, ensure_ascii=False)
            output.write("\n")
        os.replace(temp_path, destination)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    api_key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
    if not api_key:
        raise TranscriptionError("Set ASSEMBLYAI_API_KEY in the environment.")
    if not args.audio.is_file():
        raise TranscriptionError("Audio file does not exist or is not a regular file.")
    if args.audio.resolve() == args.output.resolve():
        raise TranscriptionError("Output must differ from the input audio file.")
    if not args.output.parent.is_dir():
        raise TranscriptionError("Output directory does not exist.")
    write_output(args.output, transcribe(api_key, args.audio))


if __name__ == "__main__":
    try:
        main()
    except (TranscriptionError, OSError) as exc:
        # OSError text may contain local paths, but never API response bodies or audio.
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("Interrupted; any submitted transcript remains available for review.", file=sys.stderr)
        sys.exit(130)
