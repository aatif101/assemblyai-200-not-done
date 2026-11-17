# memo-transcriber

Transcribes the weekly product memo, labels speakers, and writes a short summary.

    ./run.sh memo.wav out.json

Output: `{"transcript_id", "text", "utterances": [{"speaker", "text"}], "summary"}`.

Set `ASSEMBLYAI_API_KEY` before running. The entry point installs the pinned
Python SDK. Transcription uses Universal-3 Pro (with Universal-2 fallback), speaker
labels, and key-term prompts for Quorvex, Zyntrix Flow, and Talvanta. The two-sentence
summary uses AssemblyAI's LLM Gateway (`qwen3.5-4b-32k-fast`) with the same API key.
The command exits 0 on success and non-zero if transcription, summarization, or
writing the output fails.
