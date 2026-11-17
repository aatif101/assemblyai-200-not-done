Run with Python 3 and an exported `ASSEMBLYAI_API_KEY`:

```sh
./run.sh <audio.wav> <out.json>
```

No packages need installing. The script uploads the local recording, submits an
AssemblyAI recorded-audio transcription with `speech_models: ["universal-3-5-pro"]`
and `language_code: "en_uk"`, then polls until completion. British spelling is
applied by AssemblyAI; the returned transcript text is saved without rewriting.
See [AssemblyAI's locale documentation](https://www.assemblyai.com/docs/pre-recorded-audio/language-detection#choose-a-locale).

On success, the output contains exactly `{"transcript_id": "...", "text": "..."}`
and the process exits 0. Failures print an error to stderr and exit non-zero,
leaving any existing output file intact. HTTP requests time out after 120 seconds;
polling is limited to two hours. A fresh shell needs the API key exported, but
does not need a virtual environment or any setup commands.
