"""Transcribe support calls with server-side PII redaction. Python stdlib only."""

import argparse
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

BASE_URL = "https://api.assemblyai.com/v2"
POLICIES = ["person_name", "us_social_security_number", "phone_number"]
POLL_TIMEOUT = 24 * 60 * 60


class TranscriptionError(Exception):
    pass


def api_request(key, method, path, *, payload=None, audio=None):
    headers = {"Authorization": key}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    elif audio is not None:
        data = audio
        headers["Content-Type"] = "application/octet-stream"
        headers["Content-Length"] = str(os.fstat(audio.fileno()).st_size)
    request = Request(BASE_URL + path, data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=300) as response:
            result = json.load(response)
    except HTTPError as exc:
        # API error bodies may contain sensitive text. Never log them.
        raise TranscriptionError(f"AssemblyAI {method} {path}: HTTP {exc.code}") from None
    except (URLError, TimeoutError, OSError):
        raise TranscriptionError(f"AssemblyAI {method} {path}: connection failed or timed out") from None
    except (ValueError, UnicodeError):
        raise TranscriptionError("AssemblyAI returned invalid JSON") from None
    if not isinstance(result, dict):
        raise TranscriptionError("AssemblyAI returned an invalid response")
    return result


def transcription_config(upload_url):
    return {
        "audio_url": upload_url,
        "speech_models": ["universal-3-5-pro", "universal-2"],
        "speaker_labels": True,
        "speaker_options": {"min_speakers_expected": 2, "max_speakers_expected": 2},
        "redact_pii": True,
        "redact_pii_policies": POLICIES,
        "redact_pii_sub": "entity_name",
        "redact_pii_return_unredacted": False,
    }


def project_transcript(result, transcript_id):
    # Fail closed if the service did not apply the requested privacy settings.
    if (result.get("redact_pii") is not True
            or not set(POLICIES).issubset(result.get("redact_pii_policies") or [])
            or result.get("redact_pii_return_unredacted") is True
            or any(result.get(k) is not None for k in
                   ("unredacted_text", "unredacted_words", "unredacted_utterances"))):
        raise TranscriptionError("AssemblyAI did not confirm redaction; no output written")
    text = result.get("text")
    utterances = result.get("utterances")
    if not isinstance(text, str) or not isinstance(utterances, list):
        raise TranscriptionError("AssemblyAI did not return transcript text and speaker utterances")
    output = []
    for utterance in utterances:
        if (not isinstance(utterance, dict)
                or not isinstance(utterance.get("speaker"), str)
                or not utterance["speaker"]
                or not isinstance(utterance.get("text"), str)):
            raise TranscriptionError("AssemblyAI returned an invalid speaker utterance")
        output.append({"speaker": utterance["speaker"], "text": utterance["text"]})
    return {"transcript_id": transcript_id, "text": text, "utterances": output}


def transcribe(key, audio_path):
    with audio_path.open("rb") as audio:
        upload = api_request(key, "POST", "/upload", audio=audio)
    upload_url = upload.get("upload_url")
    if not isinstance(upload_url, str) or not upload_url.startswith("https://"):
        raise TranscriptionError("AssemblyAI did not return an upload URL")
    result = api_request(key, "POST", "/transcript", payload=transcription_config(upload_url))
    transcript_id = result.get("id")
    if not isinstance(transcript_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", transcript_id):
        raise TranscriptionError("AssemblyAI did not return a valid transcript ID")
    # Keep this ID available for review/retention even if polling fails later.
    print(f"AssemblyAI transcript_id: {transcript_id}", file=sys.stderr)
    deadline = time.monotonic() + POLL_TIMEOUT
    path = "/transcript/" + quote(transcript_id, safe="")
    while True:
        result = api_request(key, "GET", path)
        status = result.get("status")
        if status == "completed":
            return project_transcript(result, transcript_id)
        if status == "error":
            raise TranscriptionError("AssemblyAI transcription failed; review the transcript ID above")
        if status not in ("queued", "processing"):
            raise TranscriptionError("AssemblyAI returned an unexpected transcription status")
        if time.monotonic() >= deadline:
            raise TranscriptionError("Transcription exceeded 24 hours; review the transcript ID above")
        time.sleep(3)


def write_output(output_path, result):
    # Atomic replacement: a failure cannot leave a partial JSON file.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=output_path.parent,
                                         prefix=".transcript-", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(result, handle, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, output_path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
    if not key:
        raise TranscriptionError("Set ASSEMBLYAI_API_KEY in the environment")
    if not args.audio.is_file() or args.audio.stat().st_size == 0:
        raise TranscriptionError("Audio must be a non-empty local file")
    if args.audio.resolve() == args.output.resolve():
        raise TranscriptionError("Audio and output paths must differ")
    if not args.output.parent.is_dir() or args.output.is_dir():
        raise TranscriptionError("Output must name a file in an existing directory")
    write_output(args.output, transcribe(key, args.audio))


if __name__ == "__main__":
    try:
        main()
    except TranscriptionError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    except OSError:
        print("Error: unable to read audio or write output", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("Interrupted; any submitted transcript remains for your retention job", file=sys.stderr)
        sys.exit(130)
