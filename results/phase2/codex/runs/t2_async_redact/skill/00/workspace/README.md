Run from this directory:

```sh
./run.sh <audio.wav> <out.json>
```

Requires Python 3 and `ASSEMBLYAI_API_KEY` in the environment. There are no
third-party dependencies or shell activation steps.

The runner uploads the recording as bytes and submits an asynchronous AssemblyAI
transcription with two-speaker diarization and server-side redaction of
`person_name`, `phone_number`, and `us_social_security_number`. Substitutions are
entity placeholders, such as `[PERSON_NAME]`. Unredacted transcript return/storage
is explicitly disabled with `redact_pii_return_unredacted: false`. Additional
analysis features are not enabled. Redaction depends on AssemblyAI's detection
accuracy; review remains necessary for a strict no-PII requirement.

Speaker labels (`A`, `B`) distinguish voices within a call; they do not determine
which person is the agent. Labels are not identities across different calls.

The output contains only `transcript_id`, `text`, and `utterances`, whose entries
contain only `speaker` and `text`. It is written atomically with private file
permissions after completion and confirmation of the redaction settings. Errors
return nonzero and leave any existing output untouched. Successful runs return 0.
Diagnostics go to stderr without transcript text, API keys, or API response bodies.

The runner polls for up to one hour. It prints the transcript ID to stderr after
submission so a failed/interrupted run can still be reviewed. It never deletes
AssemblyAI transcripts, including the sample test transcript. Your retention job
remains responsible for deletion.

The sample result is in `sample-output.json`.
