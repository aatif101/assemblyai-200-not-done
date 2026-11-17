# memo-transcriber

Transcribes the weekly product memo, labels speakers, and writes a short summary.

    ./run.sh memo.wav out.json

Output: `{"transcript_id", "text", "utterances": [{"speaker", "text"}], "summary"}`.

Set `ASSEMBLYAI_API_KEY` before running. The launcher installs the pinned Python
SDK. Transcription uses Universal-3 Pro with speaker labels and keyterm prompts
for Quorvex, Zyntrix Flow, and Talvanta; summaries use the AssemblyAI LLM Gateway
with `qwen3.5-4b-32k-fast`. The key needs access to both services.

The command exits 0 after writing the JSON and non-zero if transcription,
summarization, or writing the output fails.
