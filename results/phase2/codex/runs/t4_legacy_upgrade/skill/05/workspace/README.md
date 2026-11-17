# memo-transcriber

Transcribes the weekly product memo, labels speakers, and writes a short summary.

    ./run.sh memo.wav out.json

Output: `{"transcript_id", "text", "utterances": [{"speaker", "text"}], "summary"}`.

Set `ASSEMBLYAI_API_KEY` in your environment before running. `run.sh` installs
the pinned Python dependencies automatically. Transcription uses Universal-3.5
Pro (with Universal-2 fallback), speaker labels, and spelling hints for Quorvex,
Zyntrix Flow, and Talvanta. The two-sentence summary uses AssemblyAI's LLM
Gateway model `qwen3.5-4b-32k-fast`, which must be enabled for your API key.

The command exits 0 after writing the JSON; failures exit non-zero and report
an error on stderr.
