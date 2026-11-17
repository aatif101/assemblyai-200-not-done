# memo-transcriber

Transcribes the weekly product memo, labels speakers, and writes a short summary.

    ./run.sh memo.wav out.json

Output: `{"transcript_id", "text", "utterances": [{"speaker", "text"}], "summary"}`.

Set `ASSEMBLYAI_API_KEY` in your environment before running. The launcher installs
the pinned Python dependency. Transcription uses Universal-3.5 Pro with Universal-2
fallback, speaker labels, and keyterms for Quorvex, Zyntrix Flow, and Talvanta.
The two-sentence summary uses the AssemblyAI LLM Gateway model
`qwen3.5-4b-32k-fast`, verified with this account.

The command exits 0 on success and non-zero on failure. Transcription and summary
must both succeed before the output file is opened.

Run regression tests with `python -m unittest discover -s tests`.
