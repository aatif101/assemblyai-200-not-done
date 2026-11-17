Run from this directory:

```sh
export ASSEMBLYAI_API_KEY='your-key'
./run.sh <audio.wav> <out.json>
```

Requires Python 3.9+ and network access to AssemblyAI. No packages need to be
installed. Success exits 0; failures exit nonzero and report a brief error to
stderr without printing transcript content. Output is atomically written with
owner-only permissions. A failed run leaves any existing output unchanged.

The request enables AssemblyAI's server-side PII redaction for `person_name`,
`phone_number`, and `us_social_security_number`, using entity placeholders such
as `[PERSON_NAME]`. It explicitly sets `redact_pii_return_unredacted` to false.
Both the full text and speaker utterances come from the redacted response; the
program does not create or store a separate unredacted transcript. The completed
response must confirm redaction and all three policies before output is written.

Speaker diarization is enabled with two expected speakers. Labels such as A/B
identify voices consistently within a call; they do not establish agent/customer
roles. The program does not guess those roles from speaking order.

Automated PII recognition can miss entities. This configuration requests all
three required redactions, but it cannot guarantee perfect detection on every
recording. If zero missed PII is a hard requirement, automated transcription
alone cannot provide that guarantee. Original audio is uploaded to AssemblyAI;
this script requests text redaction, not audio redaction.

The program never deletes transcripts from AssemblyAI, including after errors or
timeouts. Your retention job owns deletion after review. The transcript ID is
printed to stderr when available, so incomplete jobs can be located. Polling is
limited to two hours, with a 120-second timeout per network request. Failed
submissions are not automatically retried to avoid creating duplicate jobs.

The live sample output is `sample.transcript.json`. A subsequent GET from
AssemblyAI confirmed identical redacted text and utterances, with two speakers,
four name placeholders, one SSN placeholder, and one phone placeholder.

Run the privacy contract and failure-path tests with:

```sh
python3 -m unittest -q test_transcribe.py
```
