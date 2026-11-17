Run from this directory with Python 3 and `ASSEMBLYAI_API_KEY` exported:

```sh
./run.sh <audio.wav> <out.json>
```

No packages need installing. The runner uploads the original audio, submits an
asynchronous AssemblyAI transcription job, polls for up to one hour, and writes
only `transcript_id`, `text`, and `utterances` (each with `speaker` and `text`).
Success exits 0; errors exit nonzero and leave any existing output unchanged.
Output is written atomically with private file permissions. The transcript ID
is printed to stderr once submitted so a failed or interrupted job can be reviewed.

The submission enables speaker diarization with two expected speakers and
server-side PII redaction for `us_social_security_number`, `phone_number`, and
`person_name`. It explicitly sets `redact_pii_return_unredacted: false` to avoid
keeping an unredacted transcript alongside the redacted copy. Both output text
and utterances come from the redacted job. No additional analysis features are
enabled. The runner never deletes transcripts, including failed jobs; your
retention job remains responsible for deletion after review.

Speakers use AssemblyAI's anonymous labels (such as A and B). These distinguish
voices; they do not establish which person is the agent or customer.

PII detection and speaker assignment are model predictions and can miss content.
Review redaction quality before treating transcripts as free of PII. This runner
does not redact the source audio or change your account's audio retention policy.
