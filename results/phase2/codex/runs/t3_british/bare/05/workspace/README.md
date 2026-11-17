Run from this directory with Python 3 and your AssemblyAI key in the environment:

```sh
./run.sh audio.wav out.json
```

The script uploads the recording to AssemblyAI, requests Universal-2 with
`language_code: "en_uk"`, and polls until the transcript completes. That locale
requests British English spelling directly from AssemblyAI. The returned text
is saved verbatim, alongside AssemblyAI's transcript ID:

```json
{"transcript_id": "...", "text": "..."}
```

Exit status is 0 on success and non-zero on failure, with errors on stderr.
Output is replaced atomically after success; failures preserve any existing
output. Requests have a 120-second network timeout and polling has a one-hour
limit. No third-party Python packages are required.
