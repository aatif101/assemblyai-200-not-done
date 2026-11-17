Run from this directory with `ASSEMBLYAI_API_KEY` exported:

```sh
./run.sh <audio.wav> <out.json>
```

Requires Python 3 (standard library only). No installation or shell activation is
needed. Success exits 0 and atomically writes exactly `transcript_id`, `text`, and
`utterances` (each containing `speaker` and `text`). Failures exit nonzero and do
not replace the output. Diagnostics and the submitted transcript ID go to stderr;
transcript content and API credentials are never logged.

The async AssemblyAI request enables two-speaker diarization and server-side PII
redaction for `person_name`, `phone_number`, and `us_social_security_number`.
Unredacted output is explicitly disabled. Both text and utterances use the
service's redacted result. Speakers A and B distinguish voices within a call;
they do not identify the agent/customer roles or carry across calls.

No transcript deletion is performed, including on errors or interruption. Use
the returned/logged transcript ID with your review and retention job. Polling
stops after 24 hours; an unfinished job remains at AssemblyAI. Submission requests
are not retried automatically to avoid duplicate jobs and charges.

PII detection is model-based and can miss entities; review redaction quality for
your recordings before relying on it as an absolute privacy guarantee. This
configuration redacts transcripts; it does not generate redacted audio.

`sample-output.json` is the redacted output from the supplied sample recording.
