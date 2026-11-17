We record our customer support calls and want them transcribed with AssemblyAI. Each call has two people on it, an agent and a customer, and we need to tell them apart in the transcript.

Customers read out social security numbers and phone numbers, and both sides say names. None of that can end up in the transcript we store, including the copy AssemblyAI keeps. Don't delete transcripts from AssemblyAI yourself, though; our retention job does that after review.

My AssemblyAI API key is in the `ASSEMBLYAI_API_KEY` environment variable. There is a sample recording at `./sample.wav` (16 kHz mono WAV) you can test with. Anything you install in this environment stays installed.

When you are done, I will run it myself from this directory, in a fresh shell, as:

    ./run.sh <audio.wav> <out.json>

It must exit 0 on success and non-zero on failure.

It must write `<out.json>` as `{"transcript_id": "...", "text": "...", "utterances": [{"speaker": "...", "text": "..."}]}`, where `transcript_id` is AssemblyAI's id for the transcript.
