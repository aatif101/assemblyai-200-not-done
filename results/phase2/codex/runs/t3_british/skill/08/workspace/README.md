Run from this directory with Python 3 and your API key in the environment:

```sh
export ASSEMBLYAI_API_KEY='your-key'
./run.sh <audio.wav> <out.json>
```

The script uploads the recording to AssemblyAI, submits an asynchronous
Universal-3.5 Pro transcript with `language_code: "en_uk"` for British English
spelling, and polls until completion. It uses Python's standard library;
no packages need installing.

Success exits 0 and writes exactly `{"transcript_id": "...", "text": "..."}`.
Failures exit non-zero and print an error to stderr. Output is written atomically
after successful transcription. Network requests time out after 120 seconds;
transcription polling times out after one hour.

The sample result is in `sample-output.json`. Its transcript uses “organise”,
“colour”, and “centre”; the API confirmed the applied locale was `en_uk`.
