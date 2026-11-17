Run from a fresh shell with Python 3 and `ASSEMBLYAI_API_KEY` set:

```sh
./run.sh <audio.wav> <out.json>
```

No packages need to be installed. The script uploads the recording, requests
AssemblyAI transcription, and waits up to one hour for completion. It exits 0
only after atomically writing the requested JSON; errors go to stderr and exit
nonzero. An existing output file is replaced only on success.

The request enables speaker diarization with two expected speakers and
server-side PII redaction for `person_name`, `phone_number`, and
`us_social_security_number`. Detected values are replaced with entity labels
in both text and speaker utterances. `redact_pii_return_unredacted` is explicitly
false. Only the transcript ID, redacted text, and redacted speaker utterances
are written locally. Response bodies and transcript contents are never logged.
The script never deletes transcripts; AssemblyAI retains the redacted result
for your review and retention job. It prints the transcript ID to stderr so a
submitted job can also be located if polling or output writing fails.

Speakers are AssemblyAI's anonymous labels (such as A and B). These distinguish
the voices but do not identify which voice is the agent; the first speaker is
not necessarily the agent. Transcription is configured for US English.

PII detection and speaker diarization are model predictions. AssemblyAI's
redaction cannot guarantee detection of every spoken name or number. Validate
its accuracy on your calls before relying on it for an absolute no-PII policy.
