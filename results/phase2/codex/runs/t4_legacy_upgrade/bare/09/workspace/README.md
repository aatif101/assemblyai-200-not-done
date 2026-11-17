# memo-transcriber

Transcribes the weekly product memo, labels speakers, and writes a short summary.

    ./run.sh memo.wav out.json

Output: `{"transcript_id", "text", "utterances": [{"speaker", "text"}], "summary"}`.

Set `ASSEMBLYAI_API_KEY` before running. `run.sh` installs the pinned Python
dependency automatically and exits 0 on success, non-zero on failure.

Uses Universal-3.5 Pro (with Universal-2 fallback), speaker diarization, and
keyterms for Quorvex, Zyntrix Flow, and Talvanta. The two-sentence summary uses
AssemblyAI's LLM Gateway (`qwen3.5-4b-32k-fast`) with the same API key.

Offline regression checks: `python -m unittest -v`.
