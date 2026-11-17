Run from this directory with Python 3 and `ASSEMBLYAI_API_KEY` set:

```bash
./run.sh <audio.wav> <out.json>
```

No packages need installing. The script uploads the audio to AssemblyAI's US
asynchronous API, requests English transcription with two-speaker diarization,
and enables server-side redaction for `person_name`, `phone_number`, and
`us_social_security_number`. It explicitly disables returning unredacted fields.
Only the requested JSON fields are written, atomically, with owner-only file
permissions. Errors exit non-zero; success exits zero. The transcript ID is also
printed to stderr so a submitted job can be located if a later step fails.

Speaker labels such as A and B distinguish voices within each call; they do not
identify agent/customer roles. No transcript deletion requests are made. Your
retention job owns deletion, including for the sample test transcript.

The sample result is in `sample.out.json`. It was checked against a subsequent
GET of the retained AssemblyAI transcript; names, the phone number and SSN were
redacted in both text and utterances.

Redaction is automatic detection and can miss PII, so it cannot guarantee that
every sensitive value in future calls will be removed. This implementation uses
AssemblyAI's redaction at submission, rather than local cleanup that would leave
the remote transcript exposed. It does not redact the original audio or change
account-level retention or model-training settings.

Reference: https://www.assemblyai.com/docs/guardrails/redact-pii-from-transcripts
