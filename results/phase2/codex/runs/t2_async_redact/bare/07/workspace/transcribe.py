#!/usr/bin/env python3
"""Transcribe a local call with AssemblyAI's server-side PII redaction."""

import json
import os
from pathlib import Path
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request


API = "https://api.assemblyai.com/v2"
POLICIES = ["person_name", "phone_number", "us_social_security_number"]
POLL_TIMEOUT = 3600


class Failure(Exception):
    pass


def request(key, method, path, data=None, content_type="application/json", size=None):
    headers = {"authorization": key, "content-type": content_type}
    if size is not None:
        headers["content-length"] = str(size)
    req = urllib.request.Request(API + path, data=data, headers=headers, method=method)
    # Retry reads only: retrying submission after a lost response can create duplicates.
    for attempt in range(4 if method == "GET" else 1):
        try:
            with urllib.request.urlopen(req, timeout=120) as response:
                result = json.load(response)
            if not isinstance(result, dict):
                raise Failure("AssemblyAI returned an invalid response.")
            return result
        except urllib.error.HTTPError as exc:
            if method == "GET" and (exc.code == 429 or exc.code >= 500) and attempt < 3:
                time.sleep(2 ** attempt)
                continue
            # Response bodies can contain sensitive text; never log them.
            raise Failure(f"AssemblyAI {method} request failed (HTTP {exc.code}).") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            if method == "GET" and attempt < 3:
                time.sleep(2 ** attempt)
                continue
            raise Failure(f"AssemblyAI {method} request failed due to a connection error.") from None
        except (ValueError, UnicodeError):
            raise Failure("AssemblyAI returned invalid JSON.") from None


def output_from_transcript(transcript, transcript_id):
    # Refuse to persist a result unless the service confirms redaction was enabled.
    if (transcript.get("redact_pii") is not True
            or not set(POLICIES).issubset(transcript.get("redact_pii_policies") or [])
            or transcript.get("redact_pii_return_unredacted") is True):
        raise Failure("AssemblyAI did not confirm the required PII redaction settings.")
    text = transcript.get("text")
    utterances = transcript.get("utterances")
    if not isinstance(text, str) or not isinstance(utterances, list):
        raise Failure("AssemblyAI returned no text or speaker utterances.")
    cleaned = []
    for utterance in utterances:
        if (not isinstance(utterance, dict)
                or not isinstance(utterance.get("speaker"), str)
                or not isinstance(utterance.get("text"), str)):
            raise Failure("AssemblyAI returned an invalid speaker utterance.")
        cleaned.append({"speaker": utterance["speaker"], "text": utterance["text"]})
    return {"transcript_id": transcript_id, "text": text, "utterances": cleaned}


def write_output(path, result):
    # Atomic replacement avoids leaving a partial JSON file after failure.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".transcript-", delete=False) as stream:
            temporary = stream.name
            json.dump(result, stream, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def main():
    if len(sys.argv) != 3:
        raise Failure("Usage: ./run.sh <audio.wav> <out.json>")
    key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
    if not key:
        raise Failure("ASSEMBLYAI_API_KEY is required.")
    audio, output = (Path(arg).resolve() for arg in sys.argv[1:])
    if not audio.is_file() or audio.stat().st_size == 0:
        raise Failure("Audio input must be a nonempty file.")
    if audio == output or (output.exists() and os.path.samefile(audio, output)):
        raise Failure("Input and output must be different files.")
    if not output.parent.is_dir() or output.is_dir():
        raise Failure("Output must name a file in an existing directory.")

    with audio.open("rb") as stream:
        uploaded = request(key, "POST", "/upload", stream,
                           "application/octet-stream", audio.stat().st_size)
    audio_url = uploaded.get("upload_url")
    if not isinstance(audio_url, str) or not audio_url.startswith("https://"):
        raise Failure("AssemblyAI returned no valid upload URL.")
    config = {
        "audio_url": audio_url,
        "speech_models": ["universal-2"],
        "speaker_labels": True,
        "speakers_expected": 2,
        "redact_pii": True,
        "redact_pii_policies": POLICIES,
        "redact_pii_sub": "entity_name",
        "redact_pii_return_unredacted": False,
    }
    transcript = request(key, "POST", "/transcript", json.dumps(config).encode("utf-8"))
    transcript_id = transcript.get("id")
    if not isinstance(transcript_id, str) or not transcript_id:
        raise Failure("AssemblyAI returned no transcript ID.")
    print(f"AssemblyAI transcript: {transcript_id}", file=sys.stderr)
    deadline = time.monotonic() + POLL_TIMEOUT
    while True:
        status = transcript.get("status")
        if status == "completed":
            break
        if status == "error":
            raise Failure("AssemblyAI transcription failed; review its status using the transcript ID.")
        if status not in ("queued", "processing"):
            raise Failure("AssemblyAI returned an unexpected transcript status.")
        if time.monotonic() >= deadline:
            raise Failure("Timed out waiting for transcription; the transcript remains in AssemblyAI.")
        time.sleep(3)
        transcript = request(key, "GET", "/transcript/" + urllib.parse.quote(transcript_id, safe=""))

    write_output(output, output_from_transcript(transcript, transcript_id))


if __name__ == "__main__":
    try:
        main()
    except Failure as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("Interrupted; any submitted transcript remains in AssemblyAI.", file=sys.stderr)
        sys.exit(130)
    except Exception:
        # Do not expose API payloads, secrets, or transcript text through tracebacks.
        print("Error: unable to complete transcription or write the output file.", file=sys.stderr)
        sys.exit(1)
