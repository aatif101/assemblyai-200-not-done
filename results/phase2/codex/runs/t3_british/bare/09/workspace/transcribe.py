#!/usr/bin/env python3
"""Transcribe an English voice memo using AssemblyAI's British locale."""

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile

import assemblyai as aai


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    api_key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
    if not api_key:
        parser.error("ASSEMBLYAI_API_KEY is not set")
    if not args.audio.is_file():
        parser.error(f"Audio file does not exist: {args.audio}")
    if args.audio.resolve() == args.output.resolve():
        parser.error("Audio and output paths must be different")
    if not args.output.parent.is_dir():
        parser.error(f"Output directory does not exist: {args.output.parent}")

    aai.settings.api_key = api_key
    aai.settings.http_timeout = 60.0
    config = aai.TranscriptionConfig(
        # Regional spelling is applied by AssemblyAI to the actual transcript.
        language_code="en_uk",
        speech_models=["universal-3-5-pro", "universal-2"],
        punctuate=True,
        format_text=True,
    )
    transcript = aai.Transcriber(config=config).submit(str(args.audio.resolve()))
    print(f"AssemblyAI transcript: {transcript.id}", file=sys.stderr)
    transcript.wait_for_completion(poll_timeout=7200)
    if transcript.status != aai.TranscriptStatus.completed:
        raise RuntimeError(
            f"Transcription {transcript.id} failed: {transcript.error or transcript.status}"
        )
    if not transcript.id or not isinstance(transcript.text, str):
        raise RuntimeError("AssemblyAI returned an incomplete transcript")

    # Publish only complete JSON; leave an existing output intact on failure.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=args.output.parent,
            prefix=f".{args.output.name}.", suffix=".tmp", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump(
                {"transcript_id": transcript.id, "text": transcript.text},
                stream, ensure_ascii=False,
            )
            stream.write("\n")
        os.replace(temporary, args.output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Transcription interrupted", file=sys.stderr)
        sys.exit(130)
    except Exception as exc:
        print(f"Transcription failed: {exc}", file=sys.stderr)
        sys.exit(1)
