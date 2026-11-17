# memo-transcriber

Transcribes the weekly product memo, labels speakers, and writes a short summary.

    ./run.sh memo.wav out.json

Output: `{"transcript_id", "text", "utterances": [{"speaker", "text"}], "summary"}`.

Set `ASSEMBLYAI_API_KEY` before running. The launcher installs the pinned Python
SDK. Transcription uses Universal-3.5 Pro (with Universal-2 fallback), speaker
labels, and keyterms for Quorvex, Zyntrix Flow, and Talvanta. The two-sentence
summary uses `qwen3.5-4b-32k-fast` through AssemblyAI's LLM Gateway with the same
API key. The command exits 0 on success and non-zero on failure.
