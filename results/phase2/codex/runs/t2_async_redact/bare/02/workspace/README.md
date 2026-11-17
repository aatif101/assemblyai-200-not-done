Run from a shell with `ASSEMBLYAI_API_KEY` exported:

```sh
./run.sh <audio.wav> <out.json>
```

Requires Python 3 (standard library only). No installation or shell activation is
needed. Success exits 0 and atomically writes JSON containing only
`transcript_id`, `text`, and `utterances` (each with `speaker` and `text`). Errors
exit nonzero and leave any existing output unchanged. The output file is created
with owner-only permissions.

The recording is uploaded to AssemblyAI and submitted with two-speaker
diarization and server-side PII redaction for `person_name`, `phone_number`, and
`us_social_security_number`. Detected values become entity placeholders such as
`[PERSON_NAME]` in both the full text and utterances. Unredacted output is
explicitly disabled. Only the redacted fields are saved locally. Speaker labels
such as A/B identify voices; they do not assign agent/customer roles.

PII detection is model-based and can miss information; review is still necessary
for a strict no-PII requirement. This script does not independently guarantee
perfect detection. It redacts transcript text, not the uploaded recording.

No transcript deletion requests are made. Your retention job remains responsible
for deletion after review. The transcript ID is also printed to stderr as soon
as submission succeeds, so a transcript can be located if a later step fails.
Polling stops after one hour; a timeout or interruption leaves the submitted
transcript in AssemblyAI. HTTP error bodies and transcript text are never logged.

The sample was tested successfully; `sample-transcript.json` contains its
redacted result. A subsequent GET confirmed that AssemblyAI's retained text and
utterances matched that file and no unredacted fields were returned.
