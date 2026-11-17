# memo-transcriber

Transcribes the weekly product memo, labels speakers, and writes a short summary.

    ./run.sh memo.wav out.json

Output: `{"transcript_id", "text", "utterances": [{"speaker", "text"}], "summary"}`.

Set `ASSEMBLYAI_API_KEY` in your environment before running. The entry point
installs the pinned Python SDK. It uses Universal-3.5 Pro with Universal-2
fallback, speaker diarization, and keyterm prompting for Quorvex, Zyntrix Flow,
and Talvanta. Speech Understanding supplies the overall paragraph summary as
the `summary` string; its length is determined by the API.

The command exits 0 after writing the result, and non-zero if transcription,
summarization, or writing fails. Offline checks: `python -m unittest -v`.
