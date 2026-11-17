Run from this directory with Python 3 and an exported `ASSEMBLYAI_API_KEY`:

```sh
./run.sh call.wav out.json
```

No packages need to be installed. The runner uploads the audio, submits an
asynchronous transcription, and waits up to one hour. Success exits 0 and writes
only `transcript_id`, `text`, and `utterances` (each containing `speaker` and
`text`). Failures exit non-zero. The output directory must already exist.
Output is written atomically with owner-only permissions; an existing output is
replaced only on success. Status and errors go to stderr without transcript text
or the API key. The transcript ID is reported as soon as submission succeeds so
you can locate the job even if polling later fails.

Speaker diarization is enabled with two expected speakers. Labels such as `A`
and `B` distinguish voices within a call; they do not establish which voice is
the agent or the customer and are not identities across calls.

PII redaction is enabled in the original AssemblyAI request for `person_name`,
`phone_number`, and `us_social_security_number`, using entity placeholders.
`redact_pii_return_unredacted` is explicitly false so AssemblyAI retains the
redacted transcript without retaining an unredacted transcript copy. The runner
exports the redacted `text` and `utterances` and refuses output if the response
does not confirm the required redaction policies. No other analysis features
are enabled. No transcript DELETE requests or retention changes are made;
your retention job remains responsible for deletion after review.

Automated PII detection can miss entities, so it cannot guarantee that every
spoken name or number is removed. Review redaction accuracy before treating
these transcripts as free of PII. The source audio itself contains PII and is
uploaded to AssemblyAI; this runner does not request audio redaction or change
audio retention.

Validation: `python3 -m unittest -v test_transcribe.py`. The live sample output
is in `sample-output.json`; its transcript remains on AssemblyAI for your review.
