Run from a shell with `ASSEMBLYAI_API_KEY` set:

```sh
./run.sh <audio.wav> <out.json>
```

Requires Python 3; no third-party packages are needed. Uploads the recording
to AssemblyAI, requests Universal-3.5 Pro with `language_code: "en_uk"`, and
polls until completion (up to one hour). The native British English locale
applies regional spelling throughout the transcript.

On success, writes exactly `{"transcript_id": "...", "text": "..."}` and exits
0. On failure, prints an error to stderr and exits non-zero. The output file is
replaced only after transcription succeeds. Progress goes to stderr.

AssemblyAI locale documentation:
https://www.assemblyai.com/docs/pre-recorded-audio/language-detection#choose-a-locale
