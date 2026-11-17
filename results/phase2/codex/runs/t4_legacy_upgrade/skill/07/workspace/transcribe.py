"""Weekly product-memo transcriber.

Transcribes a recorded memo, labels speakers, and writes a short summary for the
team channel. Product names are boosted so they come out spelled right.

Usage: python transcribe.py <audio> <out.json>
"""
import json
import os
import sys

import assemblyai as aai

PRODUCT_NAMES = ["Quorvex", "Zyntrix Flow", "Talvanta"]


def main(audio_path: str, out_path: str) -> None:
    api_key = os.environ.get("ASSEMBLYAI_API_KEY")
    if not api_key:
        raise RuntimeError("ASSEMBLYAI_API_KEY is required")
    aai.settings.api_key = api_key

    config = aai.TranscriptionConfig(
        speech_models=["universal-3-5-pro", "universal-2"],
        speaker_labels=True,
        keyterms_prompt=PRODUCT_NAMES,
        speech_understanding={
            "request": {
                "summarization": {"summary_type": "paragraph", "effort": "low"},
            },
        },
    )
    transcript = aai.Transcriber().transcribe(audio_path, config=config)
    if transcript.status == aai.TranscriptStatus.error:
        raise RuntimeError(f"Transcription failed: {transcript.error}")
    if transcript.status != aai.TranscriptStatus.completed or not transcript.text:
        raise RuntimeError("Transcription failed: no completed transcript text")

    # Speech Understanding returns the overall summary separately from chapters.
    understanding = transcript.json_response.get("speech_understanding") or {}
    results = understanding.get("response") or {}
    result = results.get("summarization") or {}
    summary = result.get("block_summary")
    if result.get("status") != "success" or not isinstance(summary, str) or not summary.strip():
        raise RuntimeError("Summary failed: Speech Understanding returned no successful summary")

    out = {
        "transcript_id": transcript.id,
        "text": transcript.text,
        "utterances": [{"speaker": u.speaker, "text": u.text} for u in transcript.utterances or []],
        "summary": summary.strip(),
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    try:
        main(sys.argv[1], sys.argv[2])
    except (RuntimeError, OSError, ValueError, aai.TranscriptError) as error:
        sys.exit(str(error))
