Run from this directory with Python 3 and your `ASSEMBLYAI_API_KEY` exported:

```sh
./run.sh recording.wav out.json
```

No packages need installing. The script uploads the recording to AssemblyAI,
submits it to Universal-3.5 Pro with `language_code: "en_uk"`, and polls until
completion. The locale requests British English spelling directly, including
for short English voice memos; no language detection or text rewriting is used.
See [AssemblyAI's locale documentation](https://assemblyai.com/docs/pre-recorded-audio/language-detection#choose-a-locale).

On success it atomically writes `{"transcript_id": "...", "text": "..."}` and
exits 0. Errors go to stderr and produce a non-zero exit status. HTTP requests
have a 120-second timeout and polling has a one-hour limit. A failed run leaves
any existing output file intact. Transcription uses your AssemblyAI account
and normal API billing applies.
