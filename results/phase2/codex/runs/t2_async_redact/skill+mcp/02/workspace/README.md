# Support call transcription

Requires Python 3; no packages need installing. From this directory, with
`ASSEMBLYAI_API_KEY` exported in your shell:

```sh
./run.sh <audio.wav> <out.json>
```

The script uploads the audio, submits an English transcription using
Universal-3.5 Pro (with Universal-2 fallback), and polls until completion.
It requests two speakers and identifies their roles as `Agent` and `Customer`.
It exits zero after writing the requested JSON schema, and nonzero on failure.
The transcript ID is also printed to stderr for review and retention tracking.

Names, phone numbers, and US social security numbers are redacted by AssemblyAI
at submission time using entity-name placeholders. Unredacted transcript
copies are explicitly disabled. Only the redacted text and utterances are
written locally. No transcript text or API error bodies are logged. Output is
written atomically with owner-only permissions; failures leave an existing
output file untouched. Network requests have timeouts, transient polling
failures are retried, and polling is limited to six hours.

The script never deletes AssemblyAI transcripts or configures automatic
deletion. Your retention job remains responsible for deletion after review.
This script requests text redaction; the original recording is uploaded as
provided.

PII detection and speaker identification use models and can make mistakes.
The settings and sample validation do not guarantee that every sensitive span
in every future call will be detected. Review redaction quality against your
privacy requirements before relying on fully automated storage.

Validated with `sample.wav`: `sample.out.json` contains ten utterances with
both roles, four name redactions, one phone redaction, and one SSN redaction.
A separate GET of the retained AssemblyAI transcript matched this output and
contained no unredacted fields. Also checked missing-key/argument failure
exits, rejection of responses without the required redaction settings or role
labels, exact output schema, and output file permissions.

References:
- https://assemblyai.com/docs/guardrails/redact-pii-from-transcripts
- https://assemblyai.com/docs/speech-understanding/speaker-identification
