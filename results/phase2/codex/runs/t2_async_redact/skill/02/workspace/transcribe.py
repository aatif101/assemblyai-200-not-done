#!/usr/bin/env python3
"""Transcribe support calls with server-side PII redaction and diarization."""

import argparse
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request


BASE_URL = "https://api.assemblyai.com/v2"
POLICIES = ["us_social_security_number", "phone_number", "person_name"]
POLL_TIMEOUT = 6 * 60 * 60


class TranscriptionError(Exception):
    pass


def request_json(method, path, api_key, body=None, content_type="application/json",
                 content_length=None):
    headers = {"Authorization": api_key, "Content-Type": content_type}
    if content_length is not None:
        headers["Content-Length"] = str(content_length)
    request = urllib.request.Request(
        BASE_URL + path, data=body, headers=headers, method=method
    )
    # Only reads are retried: retrying a submission could create duplicate jobs.
    for attempt in range(5):
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                result = json.load(response)
            if not isinstance(result, dict):
                raise TranscriptionError("AssemblyAI returned an invalid response.")
            return result
        except urllib.error.HTTPError as exc:
            if method == "GET" and (exc.code == 429 or exc.code >= 500) and attempt < 4:
                exc.close()
                time.sleep(2 ** (attempt + 1))
                continue
            # Do not print response bodies: they may contain sensitive text.
            code = exc.code
            exc.close()
            raise TranscriptionError(f"AssemblyAI {method} request failed (HTTP {code}).") from None
        except (urllib.error.URLError, TimeoutError, socket.timeout):
            if method == "GET" and attempt < 4:
                time.sleep(2 ** (attempt + 1))
                continue
            raise TranscriptionError("Could not reach AssemblyAI; request failed or timed out.") from None
        except (ValueError, UnicodeError):
            raise TranscriptionError("AssemblyAI returned invalid JSON.") from None


def transcribe(audio, api_key):
    with audio.open("rb") as source:
        size = os.fstat(source.fileno()).st_size
        if not size:
            raise TranscriptionError("The input audio file is empty.")
        upload = request_json("POST", "/upload", api_key, source,
                              "application/octet-stream", size)
    audio_url = upload.get("upload_url")
    if not isinstance(audio_url, str) or not audio_url:
        raise TranscriptionError("AssemblyAI did not return an upload URL.")

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
    job = request_json("POST", "/transcript", api_key, json.dumps(config).encode())
    transcript_id = job.get("id")
    if not isinstance(transcript_id, str) or not transcript_id:
        raise TranscriptionError("AssemblyAI did not return a transcript ID.")
    print(f"AssemblyAI transcript ID: {transcript_id}", file=sys.stderr)
    path = "/transcript/" + urllib.parse.quote(transcript_id, safe="")
    deadline = time.monotonic() + POLL_TIMEOUT
    while True:
        job = request_json("GET", path, api_key)
        status = job.get("status")
        if status == "completed":
            break
        if status == "error":
            raise TranscriptionError("AssemblyAI transcription failed; review the job using its ID.")
        if status not in ("queued", "processing"):
            raise TranscriptionError("AssemblyAI returned an unexpected job status.")
        if time.monotonic() >= deadline:
            raise TranscriptionError("Timed out waiting for AssemblyAI; the job is retained for review.")
        time.sleep(3)

    if (job.get("redact_pii") is not True
            or not set(POLICIES).issubset(job.get("redact_pii_policies") or [])
            or job.get("redact_pii_return_unredacted") is True
            or any(job.get(key) for key in
                   ("unredacted_text", "unredacted_words", "unredacted_utterances"))):
        raise TranscriptionError("AssemblyAI did not confirm the required redaction configuration.")
    text = job.get("text")
    if text is None:
        text = ""
    utterances = job.get("utterances") or []
    if not isinstance(text, str) or not isinstance(utterances, list) or (text and not utterances):
        raise TranscriptionError("AssemblyAI returned an invalid diarized transcript.")
    output_utterances = []
    for utterance in utterances:
        if (not isinstance(utterance, dict)
                or not isinstance(utterance.get("speaker"), str)
                or not isinstance(utterance.get("text"), str)):
            raise TranscriptionError("AssemblyAI returned an invalid utterance.")
        output_utterances.append({"speaker": utterance["speaker"], "text": utterance["text"]})
    return {"transcript_id": transcript_id, "text": text, "utterances": output_utterances}


def write_output(output, result):
    # Publish only a complete redacted result, with owner-only permissions.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=output.parent,
                                         prefix=".transcript-", delete=False) as stream:
            temporary = stream.name
            json.dump(result, stream, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, output)
    finally:
        if temporary is not None and os.path.exists(temporary):
            os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    api_key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
    try:
        if not api_key:
            raise TranscriptionError("Set ASSEMBLYAI_API_KEY in the environment.")
        if args.audio.resolve() == args.output.resolve():
            raise TranscriptionError("Input and output must be different files.")
        if not args.output.parent.is_dir():
            raise TranscriptionError("The output directory does not exist.")
        result = transcribe(args.audio, api_key)
        write_output(args.output, result)
    except TranscriptionError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except OSError:
        print("Error: could not read the audio or write the output file.", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrupted; any submitted AssemblyAI job is retained for review.", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
