"""Weekly product-memo transcriber.

Transcribes a recorded memo, labels speakers, and writes a short summary for the
team channel. Product names are boosted so they come out spelled right.

Usage: python transcribe.py <audio> <out.json>
"""
import json
import os
import sys
import urllib.request

import assemblyai as aai

PRODUCT_NAMES = ["Quorvex", "Zyntrix Flow", "Talvanta"]


def summarize(text: str) -> str:
    """Summarize via the LLM Gateway (LeMUR has been sunset)."""
    body = {
        "model": "qwen3.5-4b-32k-fast",
        "max_tokens": 1000,
        "messages": [{
            "role": "user",
            "content": "Summarize this short internal product status memo in two sentences. "
            "Reply with only the summary.\n\n<transcript>\n" + text + "\n</transcript>",
        }],
    }
    req = urllib.request.Request(
        "https://llm-gateway.assemblyai.com/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Authorization": aai.settings.api_key, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.load(resp)["choices"][0]["message"]["content"].strip()


def main(audio_path: str, out_path: str) -> None:
    aai.settings.api_key = os.environ["ASSEMBLYAI_API_KEY"]

    config = aai.TranscriptionConfig(
        speech_models=["universal-3-5-pro", "universal-2"],
        speaker_labels=True,
        keyterms_prompt=PRODUCT_NAMES,
    )
    transcript = aai.Transcriber().transcribe(audio_path, config=config)
    if transcript.status == aai.TranscriptStatus.error:
        sys.exit(f"Transcription failed: {transcript.error}")

    summary = summarize(transcript.text or "")

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
