"""Weekly product-memo transcriber.

Transcribes a recorded memo, labels speakers, and writes a short summary for the
team channel. Product names are boosted so they come out spelled right.

Usage: python transcribe.py <audio> <out.json>
"""
import json
import os
import sys

import assemblyai as aai
import httpx

PRODUCT_NAMES = ["Quorvex", "Zyntrix Flow", "Talvanta"]

GATEWAY_URL = "https://llm-gateway.assemblyai.com/v1/chat/completions"
SUMMARY_MODEL = "gemma-4-31b"


def summarize(text: str) -> str:
    resp = httpx.post(
        GATEWAY_URL,
        headers={"Authorization": os.environ["ASSEMBLYAI_API_KEY"]},
        json={
            "model": SUMMARY_MODEL,
            "max_tokens": 300,
            "messages": [{
                "role": "user",
                "content": "The following is a transcript of a short internal product status memo. "
                           "Summarize it in two sentences. Reply with only the summary.\n\n" + text,
            }],
        },
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"].strip()


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
