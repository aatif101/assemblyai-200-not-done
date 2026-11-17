Run from this directory with Python 3 and your exported API key:

```sh
export ASSEMBLYAI_API_KEY=your_key
./run.sh <audio.wav> <out.json>
```

No packages need installing. Success exits 0; errors exit nonzero. The output
contains only `transcript_id`, `text`, and `utterances` with `speaker` and `text`.
The output file is written atomically with owner-only permissions. Existing
output is replaced on success and left untouched on failure.

The async AssemblyAI request enables speaker diarization with exactly two
expected speakers, and server-side PII redaction for `person_name`,
`phone_number`, and `us_social_security_number`. PII is replaced with entity
placeholders, such as `[PERSON_NAME]`. Unredacted transcript output is explicitly
disabled. The program retrieves the retained transcript through AssemblyAI's
GET endpoint and checks the redaction settings before saving. It does not enable
additional analysis features that might expose PII in other fields, log transcript
text, or delete AssemblyAI transcripts. The transcript ID is printed to stderr
after submission so a failed or interrupted job can still be reviewed.

Speaker labels `A` and `B` distinguish voices within each call; they do not
identify which person is the agent or customer. Speaker letters may change
between calls.

PII detection and diarization are probabilistic: the configured policies cannot
guarantee that every spoken identifier is recognized. Review remains necessary
for a strict requirement that no PII ever persists. Local postprocessing cannot
correct a missed identifier in AssemblyAI's retained transcript. This tool does
not change project retention settings or redact/delete the original recording.

The sample run is saved in `sample.out.json`. It completed successfully with
10 utterances, two speakers, and placeholders for names, a phone number, and an
SSN. Failure checks covered missing credentials, incorrect usage, and refusal to
save responses with missing privacy settings or unredacted fields.
