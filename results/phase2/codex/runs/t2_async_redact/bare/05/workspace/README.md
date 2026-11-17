Run from this directory with Python 3 installed (no additional packages needed):

```sh
export ASSEMBLYAI_API_KEY='your-key'
./run.sh <audio.wav> <out.json>
```

The script uploads the audio and submits an English transcription with two-speaker
diarization and AssemblyAI's server-side PII redaction enabled for `person_name`,
`phone_number`, and `us_social_security_number`. It uses entity placeholders such
as `[PERSON_NAME]` in the text and speaker utterances. It checks the returned
redaction settings before writing output. Redaction is requested when creating
the transcript, so AssemblyAI's retained transcript is redacted as well.

The output contains exactly `transcript_id`, `text`, and `utterances` (each with
`speaker` and `text`). Speaker labels such as `A` and `B` distinguish voices; they
do not automatically identify agent/customer roles. The speech model is Universal-2.

Success exits 0. Errors exit nonzero and leave an existing output file untouched.
Output is written atomically with owner-only permissions. The script polls for
up to one hour. Diagnostics go to stderr and include the transcript ID after
submission so a failed/interrupted run can still be reviewed. It never deletes
AssemblyAI transcripts; your retention job remains responsible for cleanup.

PII detection is probabilistic and cannot guarantee that every spoken identifier
will be detected. Review remains necessary for strict privacy requirements.
This configuration redacts transcript text, not the uploaded audio.

`sample-output.json` is the verified result of the live sample run. The retained
AssemblyAI transcript ID is included in that file for your review/retention job.
