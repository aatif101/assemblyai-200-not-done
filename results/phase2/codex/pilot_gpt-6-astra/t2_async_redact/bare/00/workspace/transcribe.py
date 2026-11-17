#!/usr/bin/env python3
"""Submit server-redacted transcripts; leave retention to the caller's job."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import time

import httpx

API = "https://api.assemblyai.com/v2"
POLICIES = ["person_name", "phone_number", "us_social_security_number"]


class Failure(Exception):
    pass


def request(client, method, path, **kwargs):
    # Never log response bodies: errors may contain private transcript content.
    try:
        response = client.request(method, API + path, **kwargs)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as exc:
        raise Failure(f"AssemblyAI {method} {path}: HTTP {exc.response.status_code}") from None
    except (httpx.HTTPError, ValueError):
        raise Failure(f"AssemblyAI {method} {path}: network or response error") from None


def transcribe(client, audio):
    with audio.open("rb") as stream:
        upload = request(client, "POST", "/upload", content=stream,
                         headers={"Content-Type": "application/octet-stream"})
    if not isinstance(upload.get("upload_url"), str):
        raise Failure("AssemblyAI did not return an upload URL")
    job = request(client, "POST", "/transcript", json={
        "audio_url": upload["upload_url"],
        "speech_models": ["universal-2"],
        "speaker_labels": True,
        "speakers_expected": 2,
        "redact_pii": True,
        "redact_pii_policies": POLICIES,
        "redact_pii_sub": "entity_name",
        "redact_pii_return_unredacted": False,
    })
    transcript_id = job.get("id")
    if not isinstance(transcript_id, str) or not transcript_id:
        raise Failure("AssemblyAI did not return a transcript ID")
    print(f"AssemblyAI transcript_id: {transcript_id}", file=sys.stderr)
    deadline = time.monotonic() + 6 * 60 * 60
    while True:
        job = request(client, "GET", f"/transcript/{transcript_id}")
        status = job.get("status")
        if status == "completed":
            break
        if status == "error":
            raise Failure("AssemblyAI transcription failed; transcript retained for review")
        if status not in ("queued", "processing"):
            raise Failure("AssemblyAI returned an unexpected transcription status")
        if time.monotonic() >= deadline:
            raise Failure("Timed out waiting for AssemblyAI; transcript retained for review")
        time.sleep(3)

    if (job.get("redact_pii") is not True
            or not set(POLICIES).issubset(job.get("redact_pii_policies") or [])
            or job.get("redact_pii_return_unredacted") is True
            or any(job.get(key) is not None for key in
                   ("unredacted_text", "unredacted_words", "unredacted_utterances"))):
        raise Failure("AssemblyAI response did not confirm the required redaction")
    if not isinstance(job.get("text"), str) or not isinstance(job.get("utterances"), list):
        raise Failure("AssemblyAI response is missing text or speaker utterances")
    utterances = []
    for utterance in job["utterances"]:
        if (not isinstance(utterance.get("speaker"), str)
                or not isinstance(utterance.get("text"), str)):
            raise Failure("AssemblyAI returned an invalid speaker utterance")
        utterances.append({"speaker": utterance["speaker"], "text": utterance["text"]})
    return {"transcript_id": transcript_id, "text": job["text"], "utterances": utterances}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
    if not key:
        raise Failure("ASSEMBLYAI_API_KEY is required")
    if not args.audio.is_file():
        raise Failure("Input audio file does not exist")
    if args.audio.resolve() == args.output.resolve():
        raise Failure("Output must differ from the audio input")
    if not args.output.parent.is_dir():
        raise Failure("Output directory does not exist")
    temporary = None
    try:
        # Check output access before uploading; replace output only after success.
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                         dir=args.output.parent, delete=False) as out:
            temporary = out.name
            with httpx.Client(headers={"Authorization": key},
                              timeout=httpx.Timeout(120, connect=30)) as client:
                result = transcribe(client, args.audio)
            json.dump(result, out, ensure_ascii=False)
            out.write("\n")
        os.replace(temporary, args.output)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


if __name__ == "__main__":
    try:
        main()
    except Failure as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("Interrupted; submitted transcripts remain in AssemblyAI", file=sys.stderr)
        sys.exit(130)
    except Exception:
        print("Error: unable to finish transcription or write output", file=sys.stderr)
        sys.exit(1)
