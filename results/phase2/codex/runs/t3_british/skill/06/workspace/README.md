Run from this directory with Python 3 installed and `ASSEMBLYAI_API_KEY` exported:

```sh
./run.sh recording.wav out.json
```

No additional packages are needed. The script uploads the recording to AssemblyAI,
submits an asynchronous transcription using Universal-3.5 Pro with Universal-2
fallback, and polls until completion. Language detection is restricted to English
with `language_detection_options.localization: ["en_uk"]` to request British
spelling throughout. This uses AssemblyAI's regional spelling support.

On success it exits 0 and writes exactly `{"transcript_id": "...", "text": "..."}`.
The text comes directly from AssemblyAI. Progress and errors go to stderr.
Failures exit non-zero; the output is replaced atomically only on success.
Network requests time out after 120 seconds, and polling stops after six hours.

The included sample was tested live. `sample-transcript.json` contains its result,
including the British spellings “organise”, “colour”, “centre”, and “catalogued”.
