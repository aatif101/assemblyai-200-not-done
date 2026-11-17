Run from a shell with `ASSEMBLYAI_API_KEY` set:

```sh
./run.sh <audio.wav> <out.json>
```

Requires Python 3; no third-party packages are needed. Uploads the recording
to AssemblyAI, requests the Universal-2 model with the `en_uk` British English
locale, and polls until transcription completes (up to six hours).

On success, exits 0 and writes exactly `{"transcript_id": "...", "text": "..."}`.
On failure, reports an error to stderr and exits non-zero. The output is
replaced atomically only after successful transcription. The transcript ID
is also printed to stderr so it remains available if polling fails.
