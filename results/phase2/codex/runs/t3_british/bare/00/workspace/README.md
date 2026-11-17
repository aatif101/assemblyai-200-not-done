Run from this directory:

```bash
export ASSEMBLYAI_API_KEY='your-api-key'
./run.sh recording.wav out.json
```

Requires Python 3 and network access to `api.assemblyai.com`. No packages need
installing. The key is read from the environment on every run.

The command uploads the audio, requests Universal-2 with `language_code: "en_uk"`
for British English spelling, and polls until transcription completes. It writes
exactly `{"transcript_id": "...", "text": "..."}` using AssemblyAI's original
transcript id and returned text. No local spelling replacements are applied.

Success exits 0. Invalid arguments, API failures, transcription errors, and a
one-hour polling timeout exit non-zero with an error on stderr. Output is written
atomically after success; an existing output file is preserved on failure.

`sample-transcript.json` contains the successful test output for `sample.wav`.
