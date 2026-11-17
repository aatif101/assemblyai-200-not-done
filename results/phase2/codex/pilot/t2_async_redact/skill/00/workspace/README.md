Run from this directory with Python 3 and an exported API key:

```sh
export ASSEMBLYAI_API_KEY='your-key'
./run.sh call.wav out.json
```

No packages need installing. The runner uploads raw audio, submits an async
AssemblyAI transcription, polls to completion, and atomically writes only:

```json
{"transcript_id":"...","text":"...","utterances":[{"speaker":"A","text":"..."}]}
```

The original transcription request enables server-side PII redaction for
`person_name`, `phone_number`, and `us_social_security_number`, using entity
placeholders such as `[PERSON_NAME]`. It explicitly disables the unredacted
companion response. Redaction therefore applies to the hosted transcript as
well as the returned text and utterances. No extra analysis features are enabled.
AssemblyAI's automated PII detection can miss entities; review redaction before
using transcripts where complete removal is mandatory.

Diarization is configured for two people. Speakers retain AssemblyAI's labels
(usually `A` and `B`); those labels distinguish voices, but do not establish which
person is the agent. Role identification would require an additional feature.

The runner never deletes AssemblyAI transcripts, including on errors or
interruption. Your retention job remains responsible for deletion. It prints
the transcript ID to stderr once available, without printing transcript content
or API error bodies. Source audio is uploaded as provided; this does not produce
a redacted audio file.

Success exits 0. Invalid arguments, missing credentials, API failures, invalid
results, or output failures exit nonzero. Polling stops after six hours; any
submitted job remains in AssemblyAI. Failures leave any pre-existing output
untouched. New output has file permissions `0600`.

`sample-transcript.json` contains the sample test result. Tests:

```sh
python3 -m unittest -v test_transcribe.py
```
