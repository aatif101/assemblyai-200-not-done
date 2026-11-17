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
        raise RuntimeError("ASSEMBLYAI_API_KEY is not set")

    config = aai.TranscriptionConfig(
        speech_models=["universal-3-5-pro"],
        speaker_labels=True,
        keyterms_prompt=PRODUCT_NAMES,
        speech_understanding={
            "request": {
                "summarization": {"summary_type": "paragraph", "effort": "low"},
            },
        },
    )
    transcript = aai.Transcriber(api_key=api_key).transcribe(
        audio_path, config=config, poll_timeout=3600,
    )
    if transcript.status != aai.TranscriptStatus.completed:
        raise RuntimeError(f"Transcription failed: {transcript.error or transcript.status}")
    if not transcript.text or not transcript.text.strip():
        raise RuntimeError("Transcription failed: no speech was transcribed")

    # Speech Understanding results are nested, unlike the retired LeMUR result.
    understanding = transcript.json_response.get("speech_understanding") or {}
    summary_result = (understanding.get("response") or {}).get("summarization") or {}
    summary = summary_result.get("block_summary")
    if (
        summary_result.get("status") != "success"
        or not isinstance(summary, str)
        or not summary.strip()
    ):
        raise RuntimeError("Summary failed: Speech Understanding returned no summary")

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
    except Exception as exc:
        sys.exit(f"Memo transcription failed: {exc}")
