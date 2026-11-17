# memo-transcriber

Transcribes the weekly product memo, labels speakers, and writes a short summary.

    ./run.sh memo.wav out.json

Output: `{"transcript_id", "text", "utterances": [{"speaker", "text"}], "summary"}`.

Requires Python 3 and `ASSEMBLYAI_API_KEY` in the environment. `run.sh` installs
the pinned dependencies automatically. It exits 0 after writing the result and
non-zero if transcription, summarization, or writing the output fails.

Uses Universal-3.5 Pro (with Universal-2 fallback), speaker diarization, and
keyterms for Quorvex, Zyntrix Flow, and Talvanta. The two-sentence summary comes
from the AssemblyAI LLM Gateway using `qwen3.5-4b-32k-fast`.

Run offline regression checks with `python3 -m unittest -v`.
