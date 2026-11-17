"""Upload and transcribe support calls with AssemblyAI's server-side PII redaction."""

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


def request(api_key, method, path, data=None, content_type=None, length=None):
    headers = {"Authorization": api_key}
    if content_type:
        headers["Content-Type"] = content_type
    if length is not None:
        headers["Content-Length"] = str(length)
    req = urllib.request.Request(
        BASE_URL + path, data=data, headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        # Never log response bodies: they may contain sensitive data.
        raise TranscriptionError(f"AssemblyAI {method} {path}: HTTP {exc.code}") from None
    except (OSError, ValueError) as exc:
        raise TranscriptionError(
            f"AssemblyAI {method} {path}: network failure or invalid JSON"
        ) from None
    if not isinstance(result, dict):
        raise TranscriptionError("AssemblyAI returned an invalid response")
    return result


def transcribe(api_key, audio_path):
    with audio_path.open("rb") as audio:
        size = os.fstat(audio.fileno()).st_size
        if not size:
            raise TranscriptionError("Input audio file is empty")
        uploaded = request(
            api_key, "POST", "/upload", audio, "application/octet-stream", size
        )
    upload_url = uploaded.get("upload_url")
    if not isinstance(upload_url, str) or not upload_url:
        raise TranscriptionError("AssemblyAI did not return an upload URL")
    config = {
        "audio_url": upload_url,
        "speech_models": ["universal-3-5-pro", "universal-2"],
        "speaker_labels": True,
        "speakers_expected": 2,
        "punctuate": True,
        "format_text": True,
        "redact_pii": True,
        "redact_pii_policies": POLICIES,
        "redact_pii_sub": "entity_name",
        "redact_pii_return_unredacted": False,
    }
    transcript = request(
        api_key, "POST", "/transcript", json.dumps(config).encode(), "application/json"
    )
    transcript_id = transcript.get("id")
    if not isinstance(transcript_id, str) or not transcript_id:
        raise TranscriptionError("AssemblyAI did not return a transcript ID")
    # Keep the ID available for review/retention even if subsequent polling fails.
    print(f"AssemblyAI transcript ID: {transcript_id}", file=sys.stderr)
    endpoint = "/transcript/" + urllib.parse.quote(transcript_id, safe="")
    deadline = time.monotonic() + 24 * 60 * 60
    while True:
        status = transcript.get("status")
        if status == "completed":
            break
        if status == "error":
            raise TranscriptionError("AssemblyAI transcription failed; review it using the transcript ID")
        if status not in ("queued", "processing"):
            raise TranscriptionError("AssemblyAI returned an unexpected transcript status")
        if time.monotonic() >= deadline:
            raise TranscriptionError("Timed out waiting for AssemblyAI transcription")
        time.sleep(3)
        transcript = request(api_key, "GET", endpoint)

    # These are the server-redacted fields. Never request or save unredacted_*.
    text = transcript.get("text")
    utterances = transcript.get("utterances")
    if not isinstance(text, str) or not isinstance(utterances, list):
        raise TranscriptionError("AssemblyAI returned no transcript text or speaker utterances")
    output_utterances = []
    for utterance in utterances:
        if (
            not isinstance(utterance, dict)
            or not isinstance(utterance.get("speaker"), str)
            or not isinstance(utterance.get("text"), str)
        ):
            raise TranscriptionError("AssemblyAI returned an invalid utterance")
        output_utterances.append({"speaker": utterance["speaker"], "text": utterance["text"]})
    return {"transcript_id": transcript_id, "text": text, "utterances": output_utterances}


def write_output(output_path, result):
    # Atomic replacement avoids publishing partial JSON; temporary files are private.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=output_path.parent,
            prefix=".transcript-", suffix=".tmp", delete=False
        ) as output:
            temporary = output.name
            json.dump(result, output, ensure_ascii=False)
            output.write("\n")
        os.replace(temporary, output_path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def main():
    if len(sys.argv) != 3:
        print("Usage: ./run.sh <audio.wav> <out.json>", file=sys.stderr)
        return 2
    api_key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
    if not api_key:
        print("Error: ASSEMBLYAI_API_KEY must be set", file=sys.stderr)
        return 1
    audio_path, output_path = map(Path, sys.argv[1:])
    try:
        if audio_path.resolve() == output_path.resolve():
            raise TranscriptionError("Input and output paths must differ")
        if not output_path.parent.is_dir() or output_path.is_dir():
            raise TranscriptionError("Output must be a file in an existing directory")
        result = transcribe(api_key, audio_path)
        write_output(output_path, result)
    except (TranscriptionError, OSError):
        # OSError messages can contain filenames; keep all diagnostics free of PII.
        exc = sys.exception()
        message = str(exc) if isinstance(exc, TranscriptionError) else "Cannot read audio or write output"
        print(f"Error: {message}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrupted; transcript retained in AssemblyAI", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
