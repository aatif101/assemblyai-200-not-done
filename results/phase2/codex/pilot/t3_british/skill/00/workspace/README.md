Run from this directory with `ASSEMBLYAI_API_KEY` set in your environment:

```sh
./run.sh <audio.wav> <out.json>
```

Requires Python 3.8+ and network access to AssemblyAI. No packages need installing.

The command uploads the recording, requests `universal-3-5-pro` with
`language_code: "en_uk"` for British English spelling, and polls until completion.
It writes exactly `{"transcript_id": "...", "text": "..."}` to the output file.
The text comes directly from AssemblyAI without local spelling replacements.

Success exits 0. Missing credentials, invalid input, API errors, transcription
errors, or a one-hour polling timeout exit non-zero with a message on stderr.
The output file is replaced atomically only after successful transcription.

The supplied recording was tested with:

```sh
./run.sh sample.wav sample-output.json
```

Its transcript includes “organise”, “colour”, “centre”, and “catalogued”.
