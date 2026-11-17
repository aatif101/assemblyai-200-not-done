#!/usr/bin/env python3
"""Transcribe English audio using AssemblyAI's British English spelling locale."""

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
        raise ValueError("ASSEMBLYAI_API_KEY must be set")
    if not args.audio.is_file():
        raise ValueError(f"Audio file does not exist: {args.audio}")
    if args.audio.resolve() == args.output.resolve():
        raise ValueError("The output path must differ from the audio path")
    if not args.output.parent.is_dir():
        raise ValueError(f"Output directory does not exist: {args.output.parent}")

    aai.settings.api_key = api_key
    # Explicitly request the British locale; plain 'en' defaults to US spelling.
    # Pin a model that supports this locale instead of relying on API defaults.
    config = aai.TranscriptionConfig(
        language_code="en_uk",
        speech_models=["universal-2"],
        punctuate=True,
        format_text=True,
    )
    transcript = aai.Transcriber().transcribe(str(args.audio), config=config)
    if transcript.status != aai.TranscriptStatus.completed:
        raise RuntimeError(
            f"AssemblyAI transcript {transcript.id} failed: "
            f"{transcript.error or transcript.status}"
        )
    if not transcript.id or not isinstance(transcript.text, str):
        raise RuntimeError("AssemblyAI returned an incomplete transcript result")

    # Replace the destination only after transcription and JSON writing succeed.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=args.output.parent,
            prefix=f".{args.output.name}.", suffix=".tmp", delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(
                {"transcript_id": transcript.id, "text": transcript.text},
                handle, ensure_ascii=False,
            )
            handle.write("\n")
        os.replace(temporary, args.output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Transcription failed: {exc}", file=sys.stderr)
        sys.exit(1)
