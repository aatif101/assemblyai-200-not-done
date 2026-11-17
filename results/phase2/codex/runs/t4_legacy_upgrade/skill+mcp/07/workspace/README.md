# memo-transcriber

Transcribes the weekly product memo, labels speakers, and writes a short summary.

    ./run.sh memo.wav out.json

Output: `{"transcript_id", "text", "utterances": [{"speaker", "text"}], "summary"}`.

Set `ASSEMBLYAI_API_KEY` in your environment before running. The entry point
installs the pinned Python dependencies automatically. It uses Universal-3.5 Pro
with speaker labels and product vocabulary, then LLM Gateway for a two-sentence
summary. A successful run exits 0; failures exit non-zero and leave any existing
output file intact.

Offline regression checks: `python -m unittest -v test_transcribe`.
