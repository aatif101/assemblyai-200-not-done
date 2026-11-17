#!/usr/bin/env python3
"""Submit a two-person call with server-side PII redaction; never delete it."""

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile

import assemblyai as aai


POLICIES = [
    aai.PIIRedactionPolicy.person_name,
    aai.PIIRedactionPolicy.phone_number,
    aai.PIIRedactionPolicy.us_social_security_number,
]


def redaction_config():
    # Apply redaction at creation, including AssemblyAI's stored transcript.
    # Never request an unredacted text/words/utterances copy.
    return aai.TranscriptionConfig(
        language_code="en_us",
        speaker_labels=True,
        speakers_expected=2,
        redact_pii=True,
        redact_pii_policies=POLICIES,
        redact_pii_sub=aai.PIISubstitutionPolicy.entity_name,
        redact_pii_return_unredacted=False,
    )


def result_payload(transcript):
    if transcript.status != aai.TranscriptStatus.completed:
        # API error messages may echo input: do not log response bodies.
        raise RuntimeError("AssemblyAI did not complete the transcription")
    raw = transcript.json_response
    if not isinstance(raw, dict) or raw.get("redact_pii") is not True:
        raise RuntimeError("AssemblyAI did not confirm PII redaction")
    if not {p.value for p in POLICIES}.issubset(raw.get("redact_pii_policies") or []):
        raise RuntimeError("AssemblyAI did not confirm all required PII policies")
    if raw.get("redact_pii_return_unredacted") is True or any(
        raw.get(field) is not None
        for field in ("unredacted_text", "unredacted_words", "unredacted_utterances")
    ):
        raise RuntimeError("AssemblyAI unexpectedly returned unredacted fields")
    if not transcript.id or not isinstance(transcript.text, str):
        raise RuntimeError("AssemblyAI returned an invalid transcript")
    utterances = []
    for utterance in transcript.utterances or []:
        if not isinstance(utterance.speaker, str) or not isinstance(utterance.text, str):
            raise RuntimeError("AssemblyAI returned an invalid speaker utterance")
        utterances.append({"speaker": utterance.speaker, "text": utterance.text})
    if transcript.text.strip() and not utterances:
        raise RuntimeError("AssemblyAI returned speech without speaker utterances")
    return {"transcript_id": transcript.id, "text": transcript.text, "utterances": utterances}


def write_result(output, payload):
    # Replace atomically, with private permissions. Never persist the raw response.
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=output.parent, prefix=".transcript-", delete=False
        ) as handle:
            temp_name = handle.name
            json.dump(payload, handle, ensure_ascii=False)
            handle.write("\n")
        os.replace(temp_name, output)
    finally:
        if temp_name is not None and os.path.exists(temp_name):
            os.unlink(temp_name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    api_key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
    if not api_key:
        parser.error("ASSEMBLYAI_API_KEY must be set")
    if not args.audio.is_file():
        parser.error("audio must be a readable local file")
    if args.audio.resolve() == args.output.resolve():
        parser.error("audio and output must be different files")
    if not args.output.parent.is_dir() or args.output.is_dir():
        parser.error("output must be a file in an existing directory")
    aai.settings.api_key = api_key
    aai.settings.http_timeout = 120.0
    try:
        # Open explicitly so even a URL-looking argument is treated as a local file.
        with args.audio.open("rb") as audio:
            transcript = aai.Transcriber().submit(audio, config=redaction_config())
        print(f"AssemblyAI transcript_id: {transcript.id}", file=sys.stderr)
        transcript.wait_for_completion(poll_timeout=7200)
        write_result(args.output, result_payload(transcript))
    except KeyboardInterrupt:
        print("Interrupted; any submitted transcript remains in AssemblyAI.", file=sys.stderr)
        return 130
    except Exception as exc:
        # Avoid logging API response bodies, call text, or credentials.
        message = str(exc) if type(exc) is RuntimeError else type(exc).__name__
        print(f"Transcription failed: {message}. No output was written by this run.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
