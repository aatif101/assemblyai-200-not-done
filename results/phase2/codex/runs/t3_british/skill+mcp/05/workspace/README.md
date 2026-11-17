Run from this directory with Python 3 and an exported `ASSEMBLYAI_API_KEY`:

```sh
./run.sh <audio.wav> <out.json>
```

No packages need installing. The script uploads the recording, submits an
AssemblyAI recorded-audio transcription, and polls until completion (up to one
hour). It requests `universal-3-5-pro` with `universal-2` as fallback, English
language detection, and `language_detection_options.localization: ["en_uk"]`.
AssemblyAI applies British English spelling; the script verifies that the
returned locale is `en_uk` and saves the API's text without rewriting it.

Success writes exactly `{"transcript_id": "...", "text": "..."}` and exits 0.
Failures print an error to stderr and exit non-zero. The output is replaced
atomically only after successful transcription; an existing output is preserved
on failure. Use a different output path from the audio input.

AssemblyAI's localisation documentation:
https://www.assemblyai.com/docs/pre-recorded-audio/language-detection#choose-a-locale
