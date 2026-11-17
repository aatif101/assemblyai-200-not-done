Run with Python 3 and an exported `ASSEMBLYAI_API_KEY`:

```sh
./run.sh <audio.wav> <out.json>
```

The script uploads the audio to AssemblyAI, submits it to Universal-3.5 Pro
with `language_code: "en_uk"`, and polls until complete. British spelling is
requested directly from AssemblyAI; the returned text is saved unchanged.
See the [locale documentation](https://assemblyai.com/docs/pre-recorded-audio/language-detection#choose-a-locale).

No third-party packages are required. Success exits 0 and atomically writes
`{"transcript_id": "...", "text": "..."}` as UTF-8 JSON. Failures exit non-zero
with an error on stderr. Individual HTTP requests time out after 120 seconds;
polling times out after one hour. An existing output is replaced only on success.

Tested against `sample.wav`: `sample-output.json` contains AssemblyAI's actual
transcript ID and text, including “organise”, “colour”, and “centre”. Missing
credentials and missing input files were also checked to exit non-zero.
