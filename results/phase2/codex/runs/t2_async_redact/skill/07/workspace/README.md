Run with Python 3 (standard library only; no packages to install):

```bash
export ASSEMBLYAI_API_KEY='your-key'
./run.sh <audio.wav> <out.json>
```

The runner uploads audio, submits an asynchronous AssemblyAI transcription, polls
until completion, and writes exactly `transcript_id`, `text`, and `utterances`
(each containing `speaker` and `text`). Success exits 0; failures exit nonzero.
The output is replaced atomically only on success and has owner-only permissions.
An existing output is left untouched on failure; always check the exit status.

Speaker diarization is configured for two speakers. Labels such as A and B
distinguish voices within a call; they do not identify which person is the agent
or customer and are not stable across calls.

PII redaction is enabled on the initial AssemblyAI request for `person_name`,
`phone_number`, and `us_social_security_number`, using entity-name placeholders.
Unredacted output is explicitly disabled. No additional analysis features are
requested. Both the local output and retained AssemblyAI transcript use the
service's redacted text and utterances. Automated recognition/redaction can miss
PII, so these settings cannot guarantee that every spoken identifier is caught.
The source audio is uploaded as supplied; audio redaction is not requested.

The runner never deletes transcripts or sets a deletion schedule. It prints the
transcript ID to stderr as soon as submission succeeds, including when later
processing fails, so your review and retention job can track it. Polling stops
after 24 hours. HTTP requests have a 120-second timeout. Failed requests are not
automatically resubmitted, avoiding accidental duplicate paid transcriptions.
Error responses and transcript contents are not logged.

The sample was tested successfully; `sample.transcript.json` contains its redacted
output. A subsequent GET confirmed matching redacted text and utterances in
AssemblyAI's retained copy, with no unredacted fields populated.
