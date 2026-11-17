Run from this directory with `ASSEMBLYAI_API_KEY` exported:

```sh
./run.sh <audio.wav> <out.json>
```

Python 3 and httpx are required; httpx is already installed in this environment.
The script uploads the recording and requests two-speaker diarization and
AssemblyAI server-side PII redaction for person names, phone numbers, and US
Social Security numbers. It explicitly disables returning unredacted fields.
Only the requested JSON fields are written, atomically, after completion and
redaction configuration checks. Success exits 0; failures exit nonzero and
leave any existing output intact. Diagnostics omit transcript text and API keys.

Speakers A/B distinguish voices; they do not establish agent/customer roles.
Automated PII detection can miss entities, so these settings are not a guarantee
of perfect detection. The script checks configuration, not semantic completeness.
Original audio is uploaded; this script does not request audio redaction.

No DELETE requests are made, including on failure. The transcript ID is printed
to stderr after submission so the retention job can track it even if later steps
fail. Transcripts remain in AssemblyAI for your review and retention job.

The sample output is `sample.transcript.json`. Its text and utterances were
verified against a subsequent GET of the retained AssemblyAI transcript.
Run the privacy configuration and failure checks with
`python3 -m unittest test_transcribe.py`.
