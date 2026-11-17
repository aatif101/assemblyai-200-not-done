Run with Python 3 and an exported `ASSEMBLYAI_API_KEY`:

```sh
./run.sh <audio.wav> <out.json>
```

No packages or shell activation are required. The runner uploads raw audio to
AssemblyAI, submits an English transcription with two-speaker diarization, and
polls for completion (up to one hour). Successful runs exit 0 and atomically
write exactly `transcript_id`, `text`, and `utterances` with `speaker`/`text`.
Failures exit non-zero and leave any existing output untouched. Output files
are created with owner-only permissions. Diagnostics contain no transcript text
or API key. The transcript ID is printed to stderr for review/recovery.

Redaction runs on AssemblyAI using `person_name`, `phone_number`, and
`us_social_security_number`, with `entity_name` placeholders and
`redact_pii_return_unredacted: false`. Only redacted fields are saved, and no
additional analysis features are enabled. This applies to both speakers. Other
PII categories, such as addresses, are outside the configured policies.
The runner never deletes transcripts; the retention job can use the returned ID.
Audio redaction is not enabled: this task redacts the transcript, not the source
recording.

Speaker labels such as A/B distinguish voices within each call; they do not
establish which person is the agent or customer. Automated PII detection can
miss information, so redaction settings alone cannot guarantee every sensitive
phrase is detected. Review remains necessary for a strict no-PII requirement.

Validation: `python3 -m unittest -v test_transcribe.py`. A live sample run is
saved in `sample.out.json`; its transcript remains in AssemblyAI for review.
Docs: https://www.assemblyai.com/docs/guardrails/redact-pii-from-transcripts
