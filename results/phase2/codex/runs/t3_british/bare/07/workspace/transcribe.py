#!/usr/bin/env python3
"""Transcribe local audio with AssemblyAI's British English localisation."""

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile


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
        raise ValueError("Audio and output must be different files")
    if not args.output.parent.is_dir():
        raise ValueError(f"Output directory does not exist: {args.output.parent}")
    if args.output.is_dir():
        raise ValueError(f"Output path is a directory: {args.output}")

    import assemblyai as aai

    aai.settings.api_key = api_key
    aai.settings.http_timeout = 60.0
    config = aai.TranscriptionConfig(
        speech_models=["universal-3-5-pro", "universal-2"],
        punctuate=True,
        format_text=True,
    )
    # Localisation controls spelling across the transcript, including when
    # automatic language detection is enabled. Do not infer it from accent.
    config.set_language_detection(
        expected_languages=["en"],
        fallback_language="en",
        localization=["en_uk"],
    )
    transcript = aai.Transcriber(config=config).transcribe(
        str(args.audio), poll_timeout=3600.0
    )
    if transcript.status != aai.TranscriptStatus.completed:
        raise RuntimeError(
            f"AssemblyAI transcription {transcript.id} failed: "
            f"{transcript.error or transcript.status}"
        )
    if not transcript.id or not isinstance(transcript.text, str):
        raise RuntimeError("AssemblyAI returned an invalid transcript")

    # Replace the destination only once a complete result has been written.
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=args.output.parent,
            prefix=f".{args.output.name}.", suffix=".tmp", delete=False,
        ) as output:
            temporary_path = output.name
            json.dump(
                {"transcript_id": transcript.id, "text": transcript.text},
                output, ensure_ascii=False,
            )
            output.write("\n")
        os.replace(temporary_path, args.output)
    finally:
        if temporary_path and os.path.exists(temporary_path):
            os.unlink(temporary_path)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Transcription interrupted", file=sys.stderr)
        sys.exit(130)
    except Exception as error:
        message = str(error)
        api_key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
        if api_key:
            message = message.replace(api_key, "[redacted]")
        print(f"Transcription failed: {message}", file=sys.stderr)
        sys.exit(1)
