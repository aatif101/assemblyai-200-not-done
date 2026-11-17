Run from this directory with Python 3 and `ASSEMBLYAI_API_KEY` set:

```sh
./run.sh <audio.wav> <out.json>
```

The runner uploads the recording, submits an asynchronous AssemblyAI job, and
polls until completion. It requests two-speaker diarization and server-side PII
redaction for person names, phone numbers, and US Social Security numbers.
Redactions use placeholders such as `[PERSON_NAME]`. Speaker labels are generic
AssemblyAI labels (A/B); they distinguish voices but do not identify job roles.

The JSON contains only `transcript_id`, `text`, and `utterances` with `speaker`
and `text`. The output is written atomically with owner-only permissions after
successful processing. Failures return a nonzero status and leave any existing
output untouched. Diagnostics and the remote transcript ID go to stderr;
transcript contents and API credentials are not logged.

Redaction is enabled on the original submission so the saved AssemblyAI
transcript is also redacted. The runner never requests unredacted companion
fields and never deletes remote transcripts, including on failure or interruption.
Your retention job remains responsible for deletion. The original source audio
is uploaded; this runner does not generate a redacted audio copy.

PII detection and speaker diarization are model-based and can make mistakes;
the redaction settings do not guarantee perfect detection on every recording.
Keep the review step in your workflow for that reason.

No third-party dependencies or shell activation are required. Network requests
time out after 120 seconds; polling stops after six hours. Transient polling
errors are retried, while submissions are not automatically repeated because
a lost response may already have created a job.
