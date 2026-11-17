# memo-transcriber

Transcribes the weekly product memo, labels speakers, and writes a short summary.

    ./run.sh memo.wav out.json

Output: `{"transcript_id", "text", "utterances": [{"speaker", "text"}], "summary"}`.

Set `ASSEMBLYAI_API_KEY` in your environment before running. The launcher installs
the pinned Python dependency automatically.

Uses Universal-3.5 Pro with speaker labels and keyterm prompting for Quorvex,
Zyntrix Flow, and Talvanta. The AssemblyAI LLM Gateway generates a two-sentence
summary using `qwen3.5-4b-32k-fast` with the same API key. Failures exit non-zero.

Run offline regression checks with `python -m unittest -v`.
