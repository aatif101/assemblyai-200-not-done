Run from this directory with Python 3 and your key exported:

```sh
export ASSEMBLYAI_API_KEY='your-key'
./run.sh sample.wav out.json
```

No packages need installation. The runner uploads audio, creates a transcript with
speaker labels and two expected speakers, and polls for up to one hour. It exits
0 after atomically writing the requested JSON; errors exit nonzero and leave any
existing output untouched. The transcript ID is printed to stderr after creation,
including when a later step fails. Requests have a 120-second timeout and creation
is not automatically retried, to avoid duplicate transcripts.

AssemblyAI performs PII redaction as part of transcription using `person_name`,
`phone_number`, and `us_social_security_number`. The retained AssemblyAI transcript
and exported text/utterances use entity placeholders. Only those redacted fields
are exported; no raw API responses are saved. No transcript deletion is performed,
even on failure; your retention job owns that lifecycle.

Speakers retain AssemblyAI's `A`/`B` labels. These distinguish voices, but do not
establish agent/customer roles; the first speaker is not necessarily the agent.
Redaction and diarization are model predictions and can miss entities or confuse
voices. If zero missed PII is a strict requirement, automated redaction alone
cannot guarantee it, including in AssemblyAI's retained copy. Validate this
limitation against your requirements before production use.
