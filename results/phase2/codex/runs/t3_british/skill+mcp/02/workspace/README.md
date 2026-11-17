Run with Python 3 and an exported `ASSEMBLYAI_API_KEY`:

```sh
./run.sh <audio.wav> <out.json>
```

No additional packages are required. The script uploads the recording, submits
an async AssemblyAI transcription using `universal-3-5-pro` and
`language_code: "en_uk"`, then polls for completion. The UK locale requests
British spelling directly from AssemblyAI without rewriting the transcript.

On success it atomically writes `{"transcript_id": "...", "text": "..."}`
and exits 0. Input, network, API, transcription, and output errors exit non-zero
and print a diagnostic to stderr. Polling times out after one hour; individual
HTTP requests have a five-minute timeout. An existing output is preserved on
failure.

Locale documentation:
https://www.assemblyai.com/docs/pre-recorded-audio/language-detection#choose-a-locale
