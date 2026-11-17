# Support call transcription

Run from this directory with Python 3 and an exported `ASSEMBLYAI_API_KEY`:

```sh
./run.sh <audio.wav> <out.json>
```

No packages or shell activation are required. The runner exits 0 only after
writing the complete JSON result; errors exit nonzero. Diagnostics go to stderr
without transcript text or API response bodies. The output file has owner-only
permissions and is replaced atomically on success. An existing output is left
untouched on failure, so always check the exit status.

The runner uploads the recording to AssemblyAI, submits an asynchronous
Universal-3.5 Pro transcription, and polls until completion. It sets
`speaker_labels: true` and `speakers_expected: 2`. Speaker labels such as `A`
and `B` distinguish the people consistently within a call; they do not identify
agent/customer roles, and have no meaning across calls.

PII redaction is enabled in the original AssemblyAI transcription request for
`person_name`, `us_social_security_number`, and `phone_number`, using entity
placeholders. It explicitly disables returning unredacted transcripts and
exports only the redacted `text` and `utterances`. No additional analysis
features are enabled. No DELETE requests or retention changes are made; the
transcript ID is included in the output and logged to stderr for review and
your retention job, including if processing subsequently fails.

Automatic PII detection can miss information; it cannot guarantee that every
sensitive value in arbitrary recordings will be detected. Keep your review
step. These policies do not redact other categories such as street addresses,
and they do not remove PII from the source audio.

Validation: `./run.sh sample.wav sample.out.json` succeeded. A subsequent GET of
the saved AssemblyAI transcript matched the exported text and utterances,
contained both speaker labels, and used placeholders for names, SSN, and phone
number. The remote transcript was retained. Mock checks covered request privacy
settings, polling, unsafe response rejection, API failure handling, exact JSON
fields, and output file permissions. A missing API key returned exit code 1.

AssemblyAI documentation:
https://www.assemblyai.com/docs/guardrails/redact-pii-from-transcripts
