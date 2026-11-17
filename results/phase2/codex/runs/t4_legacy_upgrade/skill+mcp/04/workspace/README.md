# memo-transcriber

Transcribes the weekly product memo, labels speakers, and writes a short summary.

    ./run.sh memo.wav out.json

Output: `{"transcript_id", "text", "utterances": [{"speaker", "text"}], "summary"}`.

Set `ASSEMBLYAI_API_KEY` in your environment before running. `run.sh` installs
the pinned Python SDK automatically. It exits 0 after writing the JSON and
non-zero if transcription, summary generation, or writing the output fails.

Uses Universal-3.5 Pro with speaker labels and product keyterms, then the
AssemblyAI LLM Gateway (`qwen3.5-4b-32k-fast`) for a two-sentence summary.

Run regression checks with `python -m unittest -v`.
