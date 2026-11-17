Run from this directory with Python 3 and `ASSEMBLYAI_API_KEY` exported:

```sh
./run.sh sample.wav out.json
```

No packages need installing. The script uploads the audio as raw bytes, submits
an asynchronous Universal-3.5 Pro transcription with `language_code: "en_uk"`,
and polls until completion. This selects British English spelling in AssemblyAI
itself, including words beyond the examples in the request.

See [AssemblyAI's locale documentation](https://assemblyai.com/docs/pre-recorded-audio/language-detection#choose-a-locale).
For known English audio, that documentation recommends setting `language_code`
directly rather than enabling automatic language detection.

The output contains exactly `transcript_id` and `text`. Success exits 0;
errors go to stderr and exit non-zero. The output is replaced only after a
successful transcription. HTTP requests have timeouts, and polling stops after
one hour. A failed run preserves any existing output file.
