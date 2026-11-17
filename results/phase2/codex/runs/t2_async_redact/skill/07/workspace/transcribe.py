#!/usr/bin/env python3
"""Upload and transcribe a two-person call with server-side PII redaction."""

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


def request(api_key, method, endpoint, data=None, content_type="application/json",
            content_length=None):
    headers = {"Authorization": api_key, "Content-Type": content_type}
    if content_length is not None:
        headers["Content-Length"] = str(content_length)
    req = urllib.request.Request(
        BASE_URL + endpoint, data=data, headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        # Response bodies can contain source text; never log them.
        raise TranscriptionError(f"AssemblyAI {method} {endpoint}: HTTP {exc.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise TranscriptionError(f"AssemblyAI {method} {endpoint}: network failure") from None
    except (ValueError, UnicodeError):
        raise TranscriptionError("AssemblyAI returned invalid JSON") from None
    if not isinstance(result, dict):
        raise TranscriptionError("AssemblyAI returned an invalid response")
    return result


def transcribe(api_key, audio_path):
    with audio_path.open("rb") as audio:
        upload = request(api_key, "POST", "/upload", data=audio,
                         content_type="application/octet-stream",
                         content_length=os.fstat(audio.fileno()).st_size)
    audio_url = upload.get("upload_url")
    if not isinstance(audio_url, str) or not audio_url:
        raise TranscriptionError("AssemblyAI did not return an upload URL")
    config = {
        "audio_url": audio_url,
        "speech_models": ["universal-3-5-pro"],
        "speaker_labels": True,
        "speaker_options": {"min_speakers_expected": 2, "max_speakers_expected": 2},
        "redact_pii": True,
        "redact_pii_policies": POLICIES,
        "redact_pii_sub": "entity_name",
        "redact_pii_return_unredacted": False,
    }
    transcript = request(api_key, "POST", "/transcript",
                         json.dumps(config).encode("utf-8"))
    transcript_id = transcript.get("id")
    if not isinstance(transcript_id, str) or not transcript_id:
        raise TranscriptionError("AssemblyAI did not return a transcript ID")
    # Retention is managed by the caller: no DELETE requests, even on failure.
    print(f"AssemblyAI transcript ID: {transcript_id}", file=sys.stderr)
    endpoint = "/transcript/" + urllib.parse.quote(transcript_id, safe="")
    deadline = time.monotonic() + 24 * 60 * 60
    while transcript.get("status") != "completed":
        if transcript.get("status") == "error":
            raise TranscriptionError("AssemblyAI transcription failed; review using the transcript ID")
        if transcript.get("status") not in ("queued", "processing"):
            raise TranscriptionError("AssemblyAI returned an unexpected transcription status")
        if time.monotonic() >= deadline:
            raise TranscriptionError("Timed out waiting for transcription; transcript retained for review")
        time.sleep(3)
        transcript = request(api_key, "GET", endpoint)

    if (transcript.get("redact_pii") is not True
            or transcript.get("redact_pii_return_unredacted") is True
            or not set(POLICIES).issubset(transcript.get("redact_pii_policies") or [])):
        raise TranscriptionError("AssemblyAI did not confirm the required redaction settings")
    text = transcript.get("text")
    utterances = transcript.get("utterances")
    if not isinstance(text, str) or not isinstance(utterances, list):
        raise TranscriptionError("AssemblyAI returned missing text or utterances")
    output = []
    for utterance in utterances:
        if (not isinstance(utterance, dict)
                or not isinstance(utterance.get("speaker"), str)
                or not isinstance(utterance.get("text"), str)):
            raise TranscriptionError("AssemblyAI returned an invalid speaker utterance")
        output.append({"speaker": utterance["speaker"], "text": utterance["text"]})
    return {"transcript_id": transcript_id, "text": text, "utterances": output}


def write_output(output_path, result):
    # Restrict permissions and replace atomically so failures never leave partial JSON.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                         dir=output_path.parent, delete=False) as handle:
            temporary = handle.name
            json.dump(result, handle, ensure_ascii=False)
            handle.write("\n")
        os.replace(temporary, output_path)
    finally:
        if temporary is not None and os.path.exists(temporary):
            os.unlink(temporary)


def main():
    if len(sys.argv) != 3:
        print("Usage: ./run.sh <audio.wav> <out.json>", file=sys.stderr)
        return 2
    api_key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
    if not api_key:
        print("ASSEMBLYAI_API_KEY must be set", file=sys.stderr)
        return 2
    audio_path, output_path = map(Path, sys.argv[1:])
    try:
        if audio_path.resolve() == output_path.resolve():
            raise TranscriptionError("Audio and output paths must be different")
        if not audio_path.is_file() or audio_path.stat().st_size == 0:
            raise TranscriptionError("Input audio must be a nonempty file")
        if not output_path.parent.is_dir():
            raise TranscriptionError("Output directory does not exist")
        result = transcribe(api_key, audio_path)
        write_output(output_path, result)
    except TranscriptionError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except OSError:
        print("Error: unable to read audio or write output", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("Interrupted; any submitted transcript is retained for review", file=sys.stderr)
        sys.exit(130)
