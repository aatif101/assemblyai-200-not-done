Run with Python 3 and an exported `ASSEMBLYAI_API_KEY`:

```sh
./run.sh sample.wav out.json
```

The script uploads the audio to AssemblyAI, requests Universal-3 Pro with
`language_code: "en_uk"` for British spelling, and waits for completion.
It writes only `{"transcript_id": "...", "text": "..."}` to the output.
No Python packages need to be installed.

Success exits with status 0. Errors are printed to stderr and exit non-zero;
an existing output file is preserved on failure. Network requests time out
after 120 seconds, and transcription polling times out after one hour.
