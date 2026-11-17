Run from this directory with `ASSEMBLYAI_API_KEY` exported:

```sh
./run.sh <audio.wav> <out.json>
```

Requires Python 3 (already installed); no packages or shell activation needed.
Returns 0 only after writing the requested JSON. Errors return nonzero and go
to stderr. The output is replaced atomically on success; an existing output is
left intact on failure. The transcript ID is also printed to stderr as soon as
AssemblyAI creates it, so review/retention can find it even if a later step fails.

The asynchronous transcription request enables two-speaker diarization and
server-side redaction of `person_name`, `us_social_security_number`, and
`phone_number`, with readable entity placeholders. Unredacted output is disabled.
The saved text and utterances come directly from the redacted API response;
other analysis features are not requested. Transcripts remain in AssemblyAI:
this program never sends a DELETE request or changes retention settings.

Speakers are AssemblyAI's anonymous labels, such as `A` and `B`, consistent
within each call. They distinguish the participants but do not assign business
roles; `A` is not guaranteed to be the agent across calls.

Redaction and diarization are model predictions, so they can miss information.
This cannot guarantee zero PII on arbitrary recordings; review remains necessary
for a strict no-PII requirement. The original audio is uploaded for processing;
this tool redacts transcripts, not the original recording. See AssemblyAI's
[PII documentation](https://www.assemblyai.com/docs/guardrails/redact-pii-from-transcripts).

Validation: `python3 -m unittest -v`. A live sample result is in
`sample-output.json`; a subsequent GET of that transcript confirmed the retained
redacted copy matches the saved output, with both speakers and all three required
PII categories redacted.
