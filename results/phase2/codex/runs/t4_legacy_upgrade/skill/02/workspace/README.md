# memo-transcriber

Transcribes the weekly product memo, labels speakers, and writes a short summary.

    ./run.sh memo.wav out.json

Output: `{"transcript_id", "text", "utterances": [{"speaker", "text"}], "summary"}`.

Set `ASSEMBLYAI_API_KEY` in your shell before running. `run.sh` installs the
pinned Python dependencies automatically. Transcription uses Universal-3.5 Pro
with speaker labels and product keyterms (Quorvex, Zyntrix Flow, Talvanta).
The two-sentence summary uses `qwen3.5-4b-32k-fast` through AssemblyAI's LLM
Gateway, using the same API key.

The command exits 0 on success and non-zero on failure, with errors on stderr.
The output is written after transcription and summarization both succeed.

Run offline regression tests with `python -m unittest -v`.
