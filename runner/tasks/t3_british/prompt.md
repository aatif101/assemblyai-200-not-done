We transcribe recorded voice memos for a client in London. They have complained that our transcripts come back with American spelling ("favor", "analyze") and they want British English spelling throughout. Please build the transcription step with AssemblyAI so the transcripts come back the way they want.

My AssemblyAI API key is in the `ASSEMBLYAI_API_KEY` environment variable. There is a sample recording at `./sample.wav` (16 kHz mono WAV) you can test with. Anything you install in this environment stays installed.

When you are done, I will run it myself from this directory, in a fresh shell, as:

    ./run.sh <audio.wav> <out.json>

It must exit 0 on success and non-zero on failure.

It must write `<out.json>` as `{"transcript_id": "...", "text": "..."}`, where `transcript_id` is AssemblyAI's id for the transcript.
