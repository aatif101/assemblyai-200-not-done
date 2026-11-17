# memo-transcriber

Transcribes the weekly product memo, labels speakers, and writes a short summary.

    ./run.sh memo.wav out.json

Output: `{"transcript_id", "text", "utterances": [{"speaker", "text"}], "summary"}`.

Set `ASSEMBLYAI_API_KEY` in the environment. `run.sh` installs the pinned Python
dependencies. Transcription uses Universal-3.5 Pro with speaker labels and product
keyterms; the LLM Gateway (`qwen3.5-4b-32k-fast`) generates the two-sentence summary.
Success exits 0; failures exit non-zero and report an error on stderr.

Offline failure checks: `python -m unittest -v`.
