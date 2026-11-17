Run from this directory with Python 3 available and `ASSEMBLYAI_API_KEY` exported:

```bash
./run.sh <audio.wav> <out.json>
```

No third-party packages are required. The script uploads the audio to AssemblyAI,
submits an asynchronous transcription using `universal-3-5-pro` and
`language_code: "en_uk"` for British English spelling, and polls until completion.
It writes exactly `{"transcript_id": "...", "text": "..."}` and exits 0 on
success. Errors go to stderr with a non-zero exit code; existing output is
preserved on failure. Polling times out after one hour.

The sample was tested with `./run.sh sample.wav sample-output.json` and returned
British spellings including “organise”, “colour”, “centre”, and “catalogued”.
