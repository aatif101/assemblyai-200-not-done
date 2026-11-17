Run from this directory with Python 3 and `ASSEMBLYAI_API_KEY` exported:

```sh
./run.sh <audio.wav> <out.json>
```

No packages or shell activation are required. The runner uploads the audio to
AssemblyAI, submits an asynchronous Universal-3.5 Pro transcript, and polls until
completion. It exits 0 after atomically writing the requested JSON, or non-zero
on failure. Errors go to stderr; failed runs do not replace an existing output.
The transcript ID is printed to stderr after submission so jobs remain findable
if processing or output writing fails.

The request enables server-side PII redaction for `person_name`, `phone_number`,
and `us_social_security_number`, with entity placeholders such as `[PERSON_NAME]`.
Unredacted output is explicitly disabled. The output uses AssemblyAI's redacted
`text` and `utterances`; no extra analysis features are requested. Redaction is
automatic detection, so the review process should check for missed PII.

Diarization is configured for two speakers. Speaker labels such as `A` and `B`
distinguish voices within each call; they do not establish the person's role.
In the sample, A is the agent and B is the customer.

The runner never deletes AssemblyAI transcripts or sets a deletion TTL. Your
retention job remains responsible for deletion after review. The source audio
is uploaded as provided; this runner requests transcript redaction, not audio
redaction. Output files are created with owner-only permissions.

Validation:

```sh
python3 -m unittest -v test_transcribe.py
```

`sample-output.json` contains the successful live result for `sample.wav`.
