Run from this directory with Python 3 and `ASSEMBLYAI_API_KEY` exported:

```sh
./run.sh sample.wav out.json
```

The script uploads the recording to AssemblyAI, requests Universal-3.5 Pro
with `language_code: "en_uk"`, and polls until transcription completes.
AssemblyAI renders British English spelling directly. No packages need installing.
The locale changes spelling while preserving the spoken vocabulary and grammar.

On success, the output contains exactly `{"transcript_id": "...", "text": "..."}`
and the process exits 0. Errors go to stderr and return a non-zero exit code.
The output is replaced atomically only after success. Polling times out after
one hour; individual HTTP requests time out after 120 seconds.

Locale documentation: https://www.assemblyai.com/docs/pre-recorded-audio/language-detection#choose-a-locale
