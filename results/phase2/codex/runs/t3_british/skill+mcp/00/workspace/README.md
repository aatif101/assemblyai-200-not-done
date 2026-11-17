Run from this directory with Python 3 and an exported `ASSEMBLYAI_API_KEY`:

```sh
./run.sh <audio.wav> <out.json>
```

No packages need installing. The script uploads the audio as raw bytes, submits
an AssemblyAI pre-recorded transcription using `universal-3-5-pro` and
`language_code: "en_uk"`, and polls until completion (up to one hour).
The British English locale controls spelling across the transcript.
See [AssemblyAI's locale documentation](https://www.assemblyai.com/docs/pre-recorded-audio/language-detection#choose-a-locale).

The output contains only `transcript_id` (AssemblyAI's transcript ID) and `text`.
It is written atomically on success. Errors go to stderr and produce a non-zero
exit status; success exits 0. Existing output is preserved if transcription fails.
