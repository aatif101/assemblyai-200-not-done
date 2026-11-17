# British English transcription

Requires Python 3 and `ASSEMBLYAI_API_KEY` in the environment. No packages need installing.

```sh
./run.sh sample.wav out.json
```

The script uploads the audio to AssemblyAI, submits an asynchronous transcription,
and polls until completion. It selects Universal-3.5 Pro with Universal-2 as a
fallback and enables English detection with
`language_detection_options.localization: ["en_uk"]` for British spelling.
This uses AssemblyAI's regional spelling feature directly.

The output is exactly `{"transcript_id": "...", "text": "..."}`. It is written
atomically after success. Diagnostics go to stderr. Success exits 0; invalid
arguments, API/network failures, transcription errors, and a one-hour polling
timeout exit non-zero. An unsuccessful run leaves any existing output unchanged.

Tested with `sample.wav`; AssemblyAI reported `language_code: "en_uk"` and
returned “organise”, “colour”, “centre”, and “catalogued”. The result is saved in
`sample-output.json`.
