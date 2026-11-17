Run from this directory with Python 3 and ASSEMBLYAI_API_KEY exported:

```sh
./run.sh <audio.wav> <out.json>
```

No packages or shell activation are needed. Success exits 0; errors exit nonzero
and go to stderr. The output is written atomically with owner-only permissions.
An existing output is replaced only after success.

The program uploads raw audio and submits one asynchronous transcription with
two-speaker diarization and role identification (Agent/Customer). If role
identification is unavailable, AssemblyAI's generic speaker labels are preserved.
English US is configured for these support calls.

Server-side PII redaction replaces person names, phone numbers, and US social
security numbers with entity placeholders in text and utterances. Unredacted
transcript return is explicitly disabled. Only the requested transcript ID,
text, and speaker/text utterances are saved locally. No transcript content or
API error bodies are logged. No transcript deletion is performed, including
on failure; IDs are printed to stderr for review and your retention job.

PII detection and role identification use models and can make mistakes. The
sample test verifies the requested settings and redactions, but cannot establish
that arbitrary future recordings contain zero missed PII. Retain your review
step if zero leakage is required. Uploaded source audio is not modified or
redacted by this program.

The sample result is in sample-output.json. A separate GET verified that the
transcript retained by AssemblyAI has the same redacted text and utterances.

AssemblyAI reference: https://www.assemblyai.com/docs/guardrails/redact-pii-from-transcripts
