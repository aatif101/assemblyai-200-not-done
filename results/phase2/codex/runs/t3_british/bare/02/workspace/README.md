Run from this directory with `ASSEMBLYAI_API_KEY` exported:

```sh
./run.sh <audio.wav> <out.json>
```

Uploads the local recording to AssemblyAI, requests British English with
`language_code="en_uk"` and automatic language detection disabled, and waits for
completion. The output contains exactly `transcript_id` and `text`. Text is the
completed AssemblyAI transcript, with no local spelling substitutions.

Success exits 0. Failures print a message to stderr and exit non-zero; an existing
output file is replaced only after successful transcription and JSON writing.
Relative paths are resolved from your current directory.

The AssemblyAI SDK is already installed in this environment. For another Python
environment, install it with `python3 -m pip install -r requirements.txt`.
