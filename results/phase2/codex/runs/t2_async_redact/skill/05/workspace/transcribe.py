"""Submit a call with server-side PII redaction and two-speaker diarization."""

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
POLL_SECONDS = 3
JOB_TIMEOUT_SECONDS = 3600


class TranscriptionError(Exception):
    pass


def request(api_key, method, path, *, payload=None, audio=None):
    headers = {"Authorization": api_key}
    data = None
    if audio is not None:
        headers["Content-Type"] = "application/octet-stream"
        headers["Content-Length"] = str(os.fstat(audio.fileno()).st_size)
        data = audio
    elif payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(BASE_URL + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        # Never print response bodies: they can contain transcript content.
        raise TranscriptionError(f"AssemblyAI {method} request failed (HTTP {exc.code}).") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise TranscriptionError(f"AssemblyAI {method} request failed (network error).") from None
    except (ValueError, UnicodeError):
        raise TranscriptionError("AssemblyAI returned invalid JSON.") from None
    if not isinstance(result, dict):
        raise TranscriptionError("AssemblyAI returned an invalid response.")
    return result


def output_record(result, transcript_id):
    if result.get("id") != transcript_id:
        raise TranscriptionError("AssemblyAI returned a different transcript ID.")
    # Confirm the retained job used our redaction settings before storing text.
    if (result.get("redact_pii") is not True
            or not set(POLICIES).issubset(result.get("redact_pii_policies") or [])
            or result.get("redact_pii_return_unredacted", False) is not False):
        raise TranscriptionError("AssemblyAI did not confirm the required PII redaction settings.")
    if any(result.get(field) for field in
           ("unredacted_text", "unredacted_words", "unredacted_utterances")):
        raise TranscriptionError("AssemblyAI unexpectedly returned unredacted content.")
    text = result.get("text")
    utterances = result.get("utterances")
    if not isinstance(text, str) or not isinstance(utterances, list):
        raise TranscriptionError("AssemblyAI did not return text and speaker utterances.")
    clean_utterances = []
    for utterance in utterances:
        if (not isinstance(utterance, dict)
                or not isinstance(utterance.get("speaker"), str)
                or not utterance["speaker"]
                or not isinstance(utterance.get("text"), str)):
            raise TranscriptionError("AssemblyAI returned an invalid speaker utterance.")
        clean_utterances.append({"speaker": utterance["speaker"], "text": utterance["text"]})
    return {"transcript_id": transcript_id, "text": text, "utterances": clean_utterances}


def atomic_write(path, record):
    temporary = None
    try:
        # mkstemp creates a private (0600) file in the destination filesystem.
        fd, temporary = tempfile.mkstemp(prefix=".transcript-", suffix=".json", dir=path.parent)
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(record, output, ensure_ascii=False)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and os.path.exists(temporary):
            os.unlink(temporary)


def main():
    if len(sys.argv) != 3:
        raise TranscriptionError("Usage: ./run.sh <audio.wav> <out.json>")
    api_key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
    if not api_key:
        raise TranscriptionError("ASSEMBLYAI_API_KEY is required.")
    audio_path = Path(sys.argv[1])
    output_path = Path(sys.argv[2])
    if audio_path.resolve() == output_path.resolve():
        raise TranscriptionError("Input and output paths must be different.")
    if not output_path.parent.is_dir():
        raise TranscriptionError("Output directory does not exist.")
    with audio_path.open("rb") as audio:
        if os.fstat(audio.fileno()).st_size == 0:
            raise TranscriptionError("Audio file is empty.")
        upload = request(api_key, "POST", "/upload", audio=audio)
    audio_url = upload.get("upload_url")
    if not isinstance(audio_url, str) or not audio_url:
        raise TranscriptionError("AssemblyAI did not return an upload URL.")
    result = request(api_key, "POST", "/transcript", payload={
        "audio_url": audio_url,
        "speech_models": ["universal-3-5-pro", "universal-2"],
        "speaker_labels": True,
        "speaker_options": {"min_speakers_expected": 2, "max_speakers_expected": 2},
        "redact_pii": True,
        "redact_pii_policies": POLICIES,
        "redact_pii_sub": "entity_name",
        # Do not retain an unredacted transcript alongside the redacted copy.
        "redact_pii_return_unredacted": False,
    })
    transcript_id = result.get("id")
    if not isinstance(transcript_id, str) or not transcript_id:
        raise TranscriptionError("AssemblyAI did not return a transcript ID.")
    print(f"AssemblyAI transcript ID: {transcript_id}", file=sys.stderr)
    deadline = time.monotonic() + JOB_TIMEOUT_SECONDS
    while result.get("status") != "completed":
        status = result.get("status")
        if status == "error":
            raise TranscriptionError("AssemblyAI transcription failed; review the job using its transcript ID.")
        if status not in ("queued", "processing"):
            raise TranscriptionError("AssemblyAI returned an unexpected job status.")
        if time.monotonic() >= deadline:
            raise TranscriptionError("Timed out waiting for AssemblyAI; the job remains available for review.")
        time.sleep(POLL_SECONDS)
        result = request(api_key, "GET", "/transcript/" + urllib.parse.quote(transcript_id, safe=""))
    atomic_write(output_path, output_record(result, transcript_id))


if __name__ == "__main__":
    try:
        main()
    except TranscriptionError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    except OSError:
        print("Error: Could not read the audio or write the output file.", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("Interrupted; any submitted AssemblyAI job remains available for review.", file=sys.stderr)
        sys.exit(130)
