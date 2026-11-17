# memo-transcriber

Transcribes the weekly product memo, labels speakers, and writes a short summary.

    ./run.sh memo.wav out.json

Output: `{"transcript_id", "text", "utterances": [{"speaker", "text"}], "summary"}`.

Set `ASSEMBLYAI_API_KEY` in the environment before running. The script installs
the dependencies in `requirements.txt`, uses Universal-3.5 Pro with speaker labels
and product keyterms, and generates a two-sentence summary through AssemblyAI's
LLM Gateway. It exits 0 on success and non-zero on failure.

Run offline regression checks with `python -m unittest -v`.
