Run from this directory with Python 3 installed and `ASSEMBLYAI_API_KEY` exported:

```sh
./run.sh <audio.wav> <out.json>
```

The script uploads the recording to AssemblyAI, requests Universal-2 with
`language_code: "en_uk"` for British English spelling, and polls until completion.
It saves AssemblyAI's returned text directly, together with its transcript ID:

```json
{"transcript_id": "...", "text": "..."}
```

No packages need installing. Success exits 0; errors print to stderr and exit
non-zero. The output is replaced atomically only after successful transcription.
Polling times out after two hours, with a 120-second timeout per HTTP request.

The sample result is in `sample-transcript.json`. AssemblyAI returned “organise”,
“colour”, and “centre” for the supplied recording.
