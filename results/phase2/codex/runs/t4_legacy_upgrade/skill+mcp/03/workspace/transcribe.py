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
    api_key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
    if not api_key:
        sys.exit("ASSEMBLYAI_API_KEY must be set.")
    aai.settings.api_key = api_key

    config = aai.TranscriptionConfig(
        speech_models=["universal-3-5-pro"],
        speaker_labels=True,
        keyterms_prompt=PRODUCT_NAMES,
    )
    transcript = aai.Transcriber().transcribe(audio_path, config=config)
    if transcript.status == aai.TranscriptStatus.error:
        sys.exit(f"Transcription failed: {transcript.error}")

    completion = aai.LLMGateway().chat.completions.create(
        model="qwen3.5-4b-32k-fast",
        messages=[
            {
                "role": "user",
                "content": (
                    "Summarize this internal product status memo in exactly two "
                    "complete sentences. Use the first sentence for the main "
                    "product updates and the second for remaining updates or "
                    "requested actions. Preserve product names exactly as written. "
                    "Return only the two-sentence summary, without a heading or "
                    "bullets.\n\nTranscript:\n"
                ) + (transcript.text or ""),
            },
        ],
        max_tokens=512,
        temperature=0,
    )
    summary = completion.choices[0].message.content
    if not isinstance(summary, str) or not summary.strip():
        sys.exit("Summarization failed: no summary returned.")
    summary = summary.strip()

    out = {
        "transcript_id": transcript.id,
        "text": transcript.text,
        "utterances": [{"speaker": u.speaker, "text": u.text} for u in transcript.utterances or []],
        "summary": summary,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
