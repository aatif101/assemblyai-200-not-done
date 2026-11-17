#!/usr/bin/env python3
"""Transcribe support calls with server-side PII redaction. Python stdlib only."""

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


BASE_URL = "https://api.assemblyai.com/v2"
PII_POLICIES = ["person_name", "phone_number", "us_social_security_number"]


def transcript_config(audio_url):
    return {
        "audio_url": audio_url,
        "speech_models": ["universal-3-5-pro", "universal-2"],
        "language_code": "en",
        "speaker_labels": True,
        "speakers_expected": 2,
        "punctuate": True,
        "redact_pii": True,
        "redact_pii_policies": PII_POLICIES,
        "redact_pii_sub": "entity_name",
        "redact_pii_return_unredacted": False,
        "speech_understanding": {
            "request": {
                "speaker_identification": {
                    "speaker_type": "role",
                    "speakers": [{"role": "Agent"}, {"role": "Customer"}],
                }
            }
        },
    }


def request(api_key, method, path, data=None, headers=None):
    req = Request(
        BASE_URL + path,
        data=data,
        method=method,
        headers={"Authorization": api_key, **(headers or {})},
    )
    # Do not retry POSTs: a lost response could otherwise create duplicate jobs.
    for attempt in range(4):
        try:
            with urlopen(req, timeout=120) as response:
                result = json.load(response)
            if not isinstance(result, dict):
                raise RuntimeError("AssemblyAI returned an invalid response")
            return result
        except HTTPError as exc:
            if method == "GET" and exc.code in (429, 500, 502, 503, 504) and attempt < 3:
                exc.close()
                time.sleep(2 ** attempt)
                continue
            code = exc.code
            exc.close()
            # API error bodies may contain sensitive source text; never log them.
            raise RuntimeError(f"AssemblyAI {method} failed (HTTP {code})") from None
        except (URLError, TimeoutError, OSError):
            if method == "GET" and attempt < 3:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"AssemblyAI {method} failed: network error") from None
        except (ValueError, UnicodeError):
            raise RuntimeError("AssemblyAI returned invalid JSON") from None


def output_document(transcript):
    # Check redaction configuration before writing any transcript content.
    if transcript.get("redact_pii") is not True or not set(PII_POLICIES).issubset(
        transcript.get("redact_pii_policies") or []
    ):
        raise RuntimeError("AssemblyAI did not confirm required PII redaction")
    if transcript.get("redact_pii_return_unredacted") or any(
        transcript.get(key) is not None
        for key in ("unredacted_text", "unredacted_words", "unredacted_utterances")
    ):
        raise RuntimeError("Unexpected unredacted transcript fields; refusing output")
    if not isinstance(transcript.get("id"), str) or not transcript["id"]:
        raise RuntimeError("Missing transcript ID")
    if not isinstance(transcript.get("text"), str):
        raise RuntimeError("Missing transcript text")
    utterances = transcript.get("utterances")
    if not isinstance(utterances, list):
        raise RuntimeError("Missing speaker utterances")
    cleaned = []
    for utterance in utterances:
        if (
            not isinstance(utterance, dict)
            or utterance.get("speaker") not in ("Agent", "Customer")
            or not isinstance(utterance.get("text"), str)
        ):
            raise RuntimeError("Speaker role identification incomplete; refusing output")
        cleaned.append({"speaker": utterance["speaker"], "text": utterance["text"]})
    return {"transcript_id": transcript["id"], "text": transcript["text"], "utterances": cleaned}


def write_output(destination, document):
    # Commit a complete result atomically, with owner-only file permissions.
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=destination.parent, delete=False
        ) as stream:
            temp_path = Path(stream.name)
            json.dump(document, stream, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
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
        raise RuntimeError("Set ASSEMBLYAI_API_KEY in the environment")
    if args.audio.resolve() == args.output.resolve():
        raise RuntimeError("Input audio and output JSON must be different files")
    if not args.output.parent.is_dir() or args.output.is_dir():
        raise RuntimeError("Output must be a file in an existing directory")
    with args.audio.open("rb") as audio:
        size = os.fstat(audio.fileno()).st_size
        if not size:
            raise RuntimeError("Input audio is empty")
        uploaded = request(api_key, "POST", "/upload", audio, {
            "Content-Type": "application/octet-stream", "Content-Length": str(size)
        })
    audio_url = uploaded.get("upload_url")
    if not isinstance(audio_url, str) or not audio_url.startswith("https://"):
        raise RuntimeError("AssemblyAI did not return an upload URL")
    transcript = request(
        api_key, "POST", "/transcript",
        json.dumps(transcript_config(audio_url)).encode("utf-8"),
        {"Content-Type": "application/json"},
    )
    transcript_id = transcript.get("id")
    if not isinstance(transcript_id, str) or not transcript_id:
        raise RuntimeError("AssemblyAI did not return a transcript ID")
    print(f"Transcript ID: {transcript_id}", file=sys.stderr)
    deadline = time.monotonic() + 6 * 60 * 60
    while True:
        transcript = request(api_key, "GET", "/transcript/" + quote(transcript_id, safe=""))
        status = transcript.get("status")
        if status == "completed":
            if transcript.get("id") != transcript_id:
                raise RuntimeError("AssemblyAI returned a mismatched transcript ID")
            write_output(args.output, output_document(transcript))
            return
        if status == "error":
            raise RuntimeError("AssemblyAI transcription failed; review the transcript ID in AssemblyAI")
        if status not in ("queued", "processing"):
            raise RuntimeError("AssemblyAI returned an unexpected transcription status")
        if time.monotonic() >= deadline:
            raise RuntimeError("Timed out waiting for transcription; transcript remains in AssemblyAI")
        time.sleep(3)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("Interrupted; submitted transcripts remain in AssemblyAI", file=sys.stderr)
        sys.exit(130)
