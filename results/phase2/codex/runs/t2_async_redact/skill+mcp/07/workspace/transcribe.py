#!/usr/bin/env python3
"""Transcribe support calls with server-side PII redaction. Python stdlib only."""

import argparse
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request


BASE_URL = "https://api.assemblyai.com/v2"
POLICIES = ["us_social_security_number", "phone_number", "person_name"]


class Failure(Exception):
    pass


def request(key, method, endpoint, body=None, content_type="application/json", size=None):
    headers = {"Authorization": key, "Content-Type": content_type}
    if size is not None:
        headers["Content-Length"] = str(size)
    req = urllib.request.Request(BASE_URL + endpoint, data=body, headers=headers, method=method)
    # Only GETs are retried: a lost POST response could already have created a job.
    attempts = 4 if method == "GET" else 1
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(req, timeout=120) as response:
                result = json.load(response)
            if not isinstance(result, dict):
                raise Failure("AssemblyAI returned an invalid response.")
            return result
        except urllib.error.HTTPError as exc:
            if method == "GET" and (exc.code == 429 or exc.code >= 500) and attempt + 1 < attempts:
                exc.close()
                time.sleep(2 ** (attempt + 1))
                continue
            # Never echo API response bodies: they could contain sensitive text.
            status = exc.code
            exc.close()
            raise Failure(f"AssemblyAI {method} request failed (HTTP {status}).") from None
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if attempt + 1 < attempts:
                time.sleep(2 ** (attempt + 1))
                continue
            raise Failure("AssemblyAI network request failed or timed out.") from None
        except (ValueError, UnicodeError):
            raise Failure("AssemblyAI returned invalid JSON.") from None


def transcribe(key, audio):
    with audio.open("rb") as source:
        size = os.fstat(source.fileno()).st_size
        if not size:
            raise Failure("The audio file is empty.")
        upload = request(key, "POST", "/upload", source, "application/octet-stream", size)
    upload_url = upload.get("upload_url")
    if not isinstance(upload_url, str) or not upload_url.startswith("https://"):
        raise Failure("AssemblyAI did not return an upload URL.")
    config = {
        "audio_url": upload_url,
        "speech_models": ["universal-3-5-pro", "universal-2"],
        "speaker_labels": True,
        "speaker_options": {"min_speakers_expected": 2, "max_speakers_expected": 2},
        "redact_pii": True,
        "redact_pii_policies": POLICIES,
        "redact_pii_sub": "entity_name",
        "redact_pii_return_unredacted": False,
    }
    job = request(key, "POST", "/transcript", json.dumps(config).encode("utf-8"))
    transcript_id = job.get("id")
    if not isinstance(transcript_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", transcript_id):
        raise Failure("AssemblyAI did not return a valid transcript ID.")
    print(f"AssemblyAI transcript ID: {transcript_id}", file=sys.stderr)
    deadline = time.monotonic() + 6 * 60 * 60
    while True:
        result = request(key, "GET", "/transcript/" + urllib.parse.quote(transcript_id, safe=""))
        status = result.get("status")
        if status == "completed":
            break
        if status == "error":
            raise Failure("AssemblyAI transcription failed; review the job using the transcript ID.")
        if status not in ("queued", "processing"):
            raise Failure("AssemblyAI returned an unexpected job status.")
        if time.monotonic() >= deadline:
            raise Failure("Polling timed out; the job remains available for your retention workflow.")
        time.sleep(3)

    # Fail closed if server-side redaction was not applied. Never request an
    # unredacted companion or enable analysis features that can expose PII.
    if result.get("redact_pii") is not True or not set(POLICIES).issubset(result.get("redact_pii_policies") or []):
        raise Failure("AssemblyAI did not confirm the required redaction configuration.")
    if result.get("redact_pii_return_unredacted") or any(result.get(k) for k in ("unredacted_text", "unredacted_words", "unredacted_utterances")):
        raise Failure("AssemblyAI unexpectedly returned unredacted data.")
    text = result.get("text")
    utterances = result.get("utterances")
    if not isinstance(text, str) or not isinstance(utterances, list):
        raise Failure("AssemblyAI returned missing transcript text or utterances.")
    output_utterances = []
    for utterance in utterances:
        if not isinstance(utterance, dict) or not isinstance(utterance.get("speaker"), str) or not isinstance(utterance.get("text"), str):
            raise Failure("AssemblyAI returned an invalid utterance.")
        output_utterances.append({"speaker": utterance["speaker"], "text": utterance["text"]})
    if text and not output_utterances:
        raise Failure("AssemblyAI returned speech without speaker utterances.")
    if (result.get("metadata") or {}).get("warnings"):
        print("AssemblyAI reported processing warnings; review the job using its transcript ID.", file=sys.stderr)
    return {"transcript_id": transcript_id, "text": text, "utterances": output_utterances}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
    if not key:
        raise Failure("ASSEMBLYAI_API_KEY must be set.")
    if not args.audio.is_file():
        raise Failure("The audio file does not exist or is not a regular file.")
    if args.audio.resolve() == args.output.resolve():
        raise Failure("Audio and output paths must differ.")
    # Verify the output directory before creating a billed remote job. Publish
    # atomically only after success; temporary files are private (mode 0600).
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=args.output.parent,
                                         prefix=".transcript-", suffix=".json", delete=False) as target:
            temporary = target.name
            result = transcribe(key, args.audio)
            json.dump(result, target, ensure_ascii=False)
            target.write("\n")
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, args.output)
        temporary = None
    finally:
        if temporary is not None:
            os.unlink(temporary)


if __name__ == "__main__":
    try:
        main()
    except Failure as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("Interrupted; any submitted AssemblyAI job is retained for review.", file=sys.stderr)
        sys.exit(130)
    except Exception:
        # Avoid traceback or exception strings that might include source text,
        # credentials, signed URLs, or unredacted service responses.
        print("Error: could not complete transcription or write the output file.", file=sys.stderr)
        sys.exit(1)
