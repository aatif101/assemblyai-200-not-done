"""Upload a call and store only AssemblyAI's redacted, diarized result."""

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


class CallError(Exception):
    pass


def request(method, path, key, data=None, content_type=None, length=None):
    headers = {"Authorization": key}
    if content_type:
        headers["Content-Type"] = content_type
    if length is not None:
        headers["Content-Length"] = str(length)
    req = urllib.request.Request(
        BASE_URL + path, data=data, headers=headers, method=method
    )
    # Retry safe reads only: a submission timeout may already have created a job.
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=120) as response:
                result = json.load(response)
            if not isinstance(result, dict):
                raise CallError("AssemblyAI returned an invalid response.")
            return result
        except urllib.error.HTTPError as exc:
            exc.close()
            if method == "GET" and exc.code in (429, 500, 502, 503, 504) and attempt < 3:
                time.sleep(2 ** attempt)
                continue
            # Never print response bodies: they may contain sensitive content.
            raise CallError(f"AssemblyAI {method} request failed (HTTP {exc.code}).") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            if method == "GET" and attempt < 3:
                time.sleep(2 ** attempt)
                continue
            raise CallError(f"AssemblyAI {method} request failed (network or timeout).") from None
        except (ValueError, UnicodeError):
            raise CallError("AssemblyAI returned invalid JSON.") from None


def transcribe(audio, key):
    with audio.open("rb") as stream:
        size = os.fstat(stream.fileno()).st_size
        if not size:
            raise CallError("The input audio file is empty.")
        upload = request("POST", "/upload", key, stream, "application/octet-stream", size)
    if not isinstance(upload.get("upload_url"), str) or not upload["upload_url"]:
        raise CallError("AssemblyAI did not return an upload URL.")
    config = {
        "audio_url": upload["upload_url"],
        "speech_models": ["universal-3-5-pro", "universal-2"],
        "speaker_labels": True,
        "speaker_options": {"min_speakers_expected": 2, "max_speakers_expected": 2},
        "redact_pii": True,
        "redact_pii_policies": POLICIES,
        "redact_pii_sub": "entity_name",
        "redact_pii_return_unredacted": False,
    }
    job = request("POST", "/transcript", key, json.dumps(config).encode(), "application/json")
    transcript_id = job.get("id")
    if not isinstance(transcript_id, str) or not transcript_id:
        raise CallError("AssemblyAI did not return a transcript ID.")
    print(f"Transcript ID: {transcript_id}", file=sys.stderr)
    deadline = time.monotonic() + 6 * 60 * 60
    while True:
        job = request("GET", "/transcript/" + urllib.parse.quote(transcript_id, safe=""), key)
        status = job.get("status")
        if status == "completed":
            break
        if status == "error":
            raise CallError("AssemblyAI transcription failed; review the job using its transcript ID.")
        if status not in ("queued", "processing"):
            raise CallError("AssemblyAI returned an unexpected transcript status.")
        if time.monotonic() >= deadline:
            raise CallError("Polling timed out; the job is retained in AssemblyAI for review.")
        time.sleep(3)
    # Fail closed if the service did not apply the requested privacy configuration.
    if (job.get("redact_pii") is not True
            or not set(POLICIES).issubset(job.get("redact_pii_policies") or [])
            or job.get("redact_pii_return_unredacted") is True
            or any(job.get(field) is not None for field in (
                "unredacted_text", "unredacted_words", "unredacted_utterances"))):
        raise CallError("AssemblyAI did not confirm the required redaction settings.")
    if job.get("id") != transcript_id or not isinstance(job.get("text"), str):
        raise CallError("AssemblyAI returned an invalid completed transcript.")
    utterances = job.get("utterances")
    if not isinstance(utterances, list) or (job["text"].strip() and not utterances):
        raise CallError("AssemblyAI did not return speaker-labeled utterances.")
    output_utterances = []
    for utterance in utterances:
        if (not isinstance(utterance, dict)
                or not isinstance(utterance.get("speaker"), str)
                or not utterance["speaker"]
                or not isinstance(utterance.get("text"), str)):
            raise CallError("AssemblyAI returned an invalid utterance.")
        output_utterances.append({"speaker": utterance["speaker"], "text": utterance["text"]})
    return {"transcript_id": transcript_id, "text": job["text"], "utterances": output_utterances}


def main():
    if len(sys.argv) != 3:
        raise CallError("Usage: ./run.sh <audio.wav> <out.json>")
    key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
    if not key:
        raise CallError("Set ASSEMBLYAI_API_KEY in your environment.")
    audio, output = map(Path, sys.argv[1:])
    if audio.resolve() == output.resolve():
        raise CallError("The output path must differ from the input audio path.")
    # Check the destination before creating a billable remote job. The private
    # temporary file is renamed only after a complete successful response.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=output.parent,
                                         prefix=".transcript-", delete=False) as stream:
            temporary = Path(stream.name)
            result = transcribe(audio, key)
            json.dump(result, stream, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    try:
        main()
    except CallError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    except OSError:
        print("Error: could not read the audio or write the output file.", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("Interrupted. Any submitted transcript is retained in AssemblyAI.", file=sys.stderr)
        sys.exit(130)
