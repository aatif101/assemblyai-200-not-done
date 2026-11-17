Run from a shell with `ASSEMBLYAI_API_KEY` exported:

```sh
./run.sh <audio.wav> <out.json>
```

Requires Python 3 (standard library only). The script uploads the recording to
AssemblyAI, requests Universal-2 with `language_code: "en_uk"` for British English
spelling, and waits for completion. It writes the returned transcript text and
AssemblyAI transcript ID as `{"transcript_id": "...", "text": "..."}`.

Success exits 0. Invalid inputs, API errors, failed transcription, and a one-hour
polling timeout exit non-zero with a message on stderr. Output is replaced
atomically only after successful transcription. API requests time out after
120 seconds. The API key is read from the environment on every run.
