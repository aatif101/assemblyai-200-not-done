Run from this directory:

```sh
./run.sh <audio.wav> <out.json>
```

Requires Python 3.8+ and `ASSEMBLYAI_API_KEY` in the environment. No packages
need installing. The script uploads the audio, submits a pre-recorded transcript
using Universal-3.5 Pro with `language_code: "en_uk"`, and polls until completion
(up to one hour, with a 120-second timeout per HTTP request).

AssemblyAI applies British English spelling directly. The English locale changes
spelling; it does not rewrite vocabulary or grammar. See the
[locale documentation](https://www.assemblyai.com/docs/pre-recorded-audio/language-detection#choose-a-locale).

On success, the script atomically writes exactly `{"transcript_id": "...", "text": "..."}`
and exits 0. Failures print a message to stderr and exit non-zero, preserving any
existing output file. The output directory must already exist.
