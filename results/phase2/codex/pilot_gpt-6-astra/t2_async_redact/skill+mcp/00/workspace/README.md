Run from a shell with `ASSEMBLYAI_API_KEY` exported:

```sh
./run.sh <audio.wav> <out.json>
```

Requires Python 3; no packages or shell activation are needed. Success exits 0;
errors exit nonzero. Output is atomically replaced only after completion and
validation, with owner-only permissions. A previous output is preserved on
failure, so always check the exit status. The transcript ID is printed to stderr
after submission, including when later polling or output writing fails.

The async AssemblyAI request enables speaker diarization with two expected
speakers and server-side PII redaction for `person_name`, `phone_number`, and
`us_social_security_number`. It explicitly disables returning unredacted data.
Only the redacted `text` and redacted `utterances` are exported, alongside the ID.
Labels such as A and B distinguish voices within each call; they do not identify
which voice is the agent. No additional analysis features are enabled.

There are no transcript deletion calls. Your retention job owns deletion.
Polling stops after six hours; request timeouts and failures also exit nonzero.
Rerunning submits a new transcript, so use the reported ID to review an existing
job after failure before rerunning.

PII detection is model-based and can miss sensitive speech; this integration
cannot guarantee zero missed PII. Your review process remains necessary for a
strict no-PII requirement. This redacts transcript data, not the source audio.

Documentation: https://assemblyai.com/docs/guardrails/redact-pii-from-transcripts
