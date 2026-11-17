Run from this directory with Python 3 and an exported `ASSEMBLYAI_API_KEY`:

```sh
./run.sh <audio.wav> <out.json>
```

No packages need installing. Success exits 0; failures exit nonzero and report
an error to stderr. The output contains exactly `transcript_id`, `text`, and
`utterances` with `speaker` and `text`. Output is written atomically with private
file permissions. An existing output file is replaced only on success.

The request enables server-side PII redaction for `person_name`, `phone_number`,
and `us_social_security_number`, using entity placeholders. It explicitly disables
returning unredacted results. No additional transcript analysis is requested.
Speaker labels A/B distinguish voices; they do not identify which voice is the
agent or customer. The diarization request specifies two speakers.

The runner never deletes AssemblyAI transcripts. It prints the submitted ID to
stderr so a job can be reviewed even if polling or output writing fails. Your
retention job remains responsible for deletion. Polling stops after six hours;
each HTTP request has a 120-second timeout. Only reads are retried.

AssemblyAI's redaction uses automated detection and can miss PII; the configuration
does not provide a mathematical guarantee that every spoken identifier is caught.
The original recording is uploaded for processing; this runner requests text
redaction, not a redacted audio file.

Validation: `python3 -m unittest -v`. A live run on `sample.wav` produced
`sample-output.json`; a subsequent GET confirmed matching redacted text and
utterances in AssemblyAI, with speakers A/B and name, phone, and SSN placeholders.
The sample transcript remains in AssemblyAI for review and retention processing.
