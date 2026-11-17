Run from this directory with Python 3 and your API key in the environment:

```sh
./run.sh <audio.wav> <out.json>
```

No packages need to be installed. The script uploads the audio as raw bytes,
submits an asynchronous transcription, and polls for completion. It exits 0
only after atomically writing the requested JSON; failures exit nonzero and
leave an existing output file intact. The new output file has private permissions.

The request enables two-speaker diarization and server-side PII redaction for
`person_name`, `phone_number`, and `us_social_security_number`. It explicitly
disables returning the unredacted transcript. Redactions appear as entity labels
such as `[PERSON_NAME]` in both `text` and `utterances`. Only the requested three
top-level fields are saved. Error messages never include API response bodies.

Speakers use AssemblyAI's anonymous labels (`A`, `B`). These distinguish the
voices; they do not identify which voice is the agent or customer.

The script never deletes remote transcripts, including on failure. It prints
the transcript ID to stderr once submitted so your retention job can review it.
Polling stops after six hours, and individual HTTP requests have a 120-second
timeout. A failed submission is not automatically retried because it might
already have created a remote job.

PII detection is model-based and can miss information; it cannot provide an
absolute guarantee that every spoken identifier is detected. Redaction is
configured on AssemblyAI's job, rather than applied only to a local copy.
Source audio is uploaded without audio redaction.

The sample was tested successfully; `sample-output.json` contains the redacted
result. Its retained AssemblyAI transcript matched the saved text and utterances,
with two speakers and redactions for all three requested categories.

API behavior: https://www.assemblyai.com/docs/guardrails/redact-pii-from-transcripts
