# memo-transcriber

Transcribes the weekly product memo, labels speakers, and writes a short summary.

    ./run.sh memo.wav out.json

Output: `{"transcript_id", "text", "utterances": [{"speaker", "text"}], "summary"}`.

Set `ASSEMBLYAI_API_KEY` in the environment before running. `run.sh` installs the
pinned Python dependencies automatically. It exits 0 on success and non-zero on
failure, with errors written to stderr.

Transcription uses Universal-3.5 Pro (with Universal-2 fallback), speaker labels,
and keyterms for Quorvex, Zyntrix Flow, and Talvanta. The two-sentence summary uses
the AssemblyAI LLM Gateway's `qwen3.5-4b-32k-fast` model, verified with this account.

Run offline regression checks with `python -m unittest -v` after dependencies are
installed.
