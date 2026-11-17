Run from this directory with Python 3 and your API key exported:

```sh
export ASSEMBLYAI_API_KEY='your-key'
./run.sh audio.wav out.json
```

No packages need installing. Success exits 0 and atomically writes exactly
`transcript_id`, `text`, and `utterances` (each with `speaker` and `text`). Errors
exit nonzero and leave an existing output file unchanged. The output file is
created with owner-only permissions. Progress and errors go to stderr without
transcript content or API response bodies.

The runner uploads raw audio, submits an asynchronous transcription, and polls
for up to one hour. It enables two-speaker diarization and server-side redaction
of `person_name`, `phone_number`, and `us_social_security_number`, using labeled
placeholders. It explicitly sets `redact_pii_return_unredacted: false` so an
unredacted transcript is not retained alongside the redacted version. Only the
redacted response text and utterances are exported; no extra analysis features
are requested. Speakers A and B distinguish voices; they do not identify which
person is the agent. Speaker assignments restart for each call.

PII detection and diarization are probabilistic. Review redacted transcripts for
missed PII before accepting them into your final store; automated redaction
cannot guarantee that every spoken name or number will be detected. This runner
configures transcript redaction, not source audio redaction or audio retention.

No transcript deletion is performed, including on errors or interruption. Once
submission succeeds, its ID is printed to stderr for review and your retention
job, even if polling or writing the output later fails. Rerunning creates a new
transcription and may incur another charge.

`sample-output.json` is the redacted result of the supplied sample. Its retained
AssemblyAI text and utterances were separately fetched and verified to match.
