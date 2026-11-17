Run from this directory with `ASSEMBLYAI_API_KEY` exported:

```sh
./run.sh <audio.wav> <out.json>
```

Requires Python 3; no packages need installing. The command uploads the audio
to AssemblyAI, submits a Universal-3.5 Pro transcription, and polls for completion.
English detection is restricted to English with an English fallback for short
memos. `language_detection_options.localization: ["en_uk"]` requests native
British English spelling.

On success, the output contains exactly `transcript_id` and `text`, and the
command exits 0. Diagnostics go to stderr. Failures exit non-zero and leave
any existing output untouched. Polling times out after one hour; each HTTP
request has a 120-second timeout.

`sample-output.json` contains the result of testing with `sample.wav`.
