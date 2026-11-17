Run from a fresh shell with `ASSEMBLYAI_API_KEY` set:

```sh
./run.sh audio.wav out.json
```

Requires Python 3; no packages need installing. Exit status is zero only after
the output JSON is written successfully. Errors go to stderr. The transcript ID
is also printed to stderr after submission, allowing review of a job if polling
or writing fails. Existing output files are replaced only on success.

Uses AssemblyAI's asynchronous API with two-speaker diarization and server-side
PII redaction for `person_name`, `phone_number`, and `us_social_security_number`.
Unredacted output is explicitly disabled. Only the redacted text and redacted
utterances are saved, alongside the transcript ID. Labels such as A and B
distinguish voices; they do not automatically identify agent/customer roles.
No transcript deletion or retention changes are performed.

PII detection is model-based and can miss entities; configuration and a passing
sample cannot guarantee that every future name or number will be detected.
Review redaction quality before storing transcripts under an absolute no-PII
requirement. The source recording is uploaded as supplied; this script requests
text redaction, not audio redaction.

`sample-output.json` is the result of the sample run. Its remote transcript is
retained for your review and retention job.
