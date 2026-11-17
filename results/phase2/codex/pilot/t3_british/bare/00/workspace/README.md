Run from this directory with Python 3.8+ and `ASSEMBLYAI_API_KEY` exported:

```sh
./run.sh sample.wav out.json
```

No additional packages are needed. The script uploads the recording to AssemblyAI,
requests Universal-2 with `language_code: "en_uk"` for British English spelling,
and polls until transcription completes. The output contains only
`transcript_id` and `text`, with AssemblyAI's returned text preserved verbatim.

Success exits 0. Errors exit non-zero and are reported on stderr. Each HTTP request
has a 60-second timeout; polling allows 30 minutes. The output is replaced
atomically on success; a failed run leaves any existing output untouched.
