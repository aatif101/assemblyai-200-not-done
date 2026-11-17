Run from this directory with Python 3 and an exported `ASSEMBLYAI_API_KEY`:

```sh
./run.sh <audio.wav> <out.json>
```

No packages need installation. The script exits 0 only after atomically writing
the requested JSON; errors go to stderr and return nonzero. Existing output is
preserved on failure. The transcript ID is logged to stderr after submission so
your retention job can track a job even if polling later fails or is interrupted.
Polling stops after one hour; the provider job remains available for review.

English support calls are submitted to AssemblyAI's asynchronous US API using
Universal-3.5 Pro with Universal-2 fallback, diarization configured for two
speakers, and server-side PII policies for `person_name`, `phone_number`, and
`us_social_security_number`. Redacted spans use entity placeholders. Only the
redacted text and utterances are written. Unredacted output is explicitly
disabled; no additional analysis features are enabled. Speaker labels such as
`A` and `B` distinguish voices; they do not identify agent/customer roles.

No transcript deletion requests are made. The retained final transcript has the
same server-side redaction as the downloaded copy. This does not redact the
source audio or change account retention or model-training settings.

PII detection and speaker diarization are model predictions and can make errors.
These settings cannot guarantee that every sensitive mention in arbitrary audio
will be detected; review is still needed for an absolute no-PII requirement.

The sample result is in `sample.transcript.json`. Run offline checks with:

```sh
python3 -m unittest -v test_transcribe.py
```

AssemblyAI reference: https://assemblyai.com/docs/guardrails/redact-pii-from-transcripts
