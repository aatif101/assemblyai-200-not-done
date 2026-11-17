#!/usr/bin/env python3
"""Transcribe local audio with AssemblyAI's British English locale."""

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path, help="Local audio file")
    parser.add_argument("output", type=Path, help="Destination JSON file")
    args = parser.parse_args()

    try:
        api_key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
        if not api_key:
            raise ValueError("ASSEMBLYAI_API_KEY must be set")
        if not args.audio.is_file():
            raise ValueError(f"Audio file does not exist: {args.audio}")
        if not args.output.parent.is_dir():
            raise ValueError(f"Output directory does not exist: {args.output.parent}")
        if args.audio.resolve() == args.output.resolve():
            raise ValueError("Audio and output paths must be different")

        import assemblyai as aai

        aai.settings.api_key = api_key
        transcript = aai.Transcriber().transcribe(
            str(args.audio),
            config=aai.TranscriptionConfig(
                # The locale controls spelling as well as English recognition.
                # Keep auto-detection off so it cannot select generic English.
                language_code="en_uk",
                language_detection=False,
                punctuate=True,
                format_text=True,
            ),
        )
        if transcript.status != aai.TranscriptStatus.completed:
            raise RuntimeError(transcript.error or f"Transcription status: {transcript.status}")
        if not transcript.id or not isinstance(transcript.text, str):
            raise RuntimeError("AssemblyAI returned an incomplete transcript")

        payload = {"transcript_id": transcript.id, "text": transcript.text}
        # Only replace the destination once the complete result is written.
        temporary_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=args.output.parent,
                prefix=f".{args.output.name}.", suffix=".tmp", delete=False,
            ) as output:
                temporary_path = output.name
                json.dump(payload, output, ensure_ascii=False)
                output.write("\n")
            os.replace(temporary_path, args.output)
        finally:
            if temporary_path is not None and os.path.exists(temporary_path):
                os.unlink(temporary_path)
        return 0
    except Exception as exc:
        print(f"Transcription failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
