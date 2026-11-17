Run from this directory with `ASSEMBLYAI_API_KEY` set:

```sh
./run.sh <audio.wav> <out.json>
```

Requires Python 3 and `httpx` (already installed here; versions are listed in
`requirements.txt`). The script uploads the file, submits a transcription with
two-speaker diarization, and waits for completion. The output contains only
`transcript_id`, `text`, and `utterances` with `speaker` and `text`.

Names, phone numbers, and US Social Security numbers are redacted by AssemblyAI
using `person_name`, `phone_number`, and `us_social_security_number` policies.
The submission explicitly sets `redact_pii_return_unredacted` to `false` and
never requests an unredacted copy. Redaction is applied on AssemblyAI's side;
the script checks the completed result's redaction configuration and refuses
to save a response containing unredacted fields. It never deletes transcripts.
The printed transcript ID can be used to locate a job even if a later step fails.

Speaker labels such as `A` and `B` distinguish voices; AssemblyAI does not identify
which voice is the agent or customer. Labels are scoped to each call. Redaction
and diarization use machine learning and can make mistakes; review redaction
quality before relying on it for an absolute privacy requirement.

Success exits 0. Invalid inputs, API errors, and transcription failures exit
nonzero. Output is written atomically with private permissions only after
completion. Existing output is left intact on failure. API response bodies and
transcript contents are never logged. No local copy of the uploaded audio is
created; transcript redaction does not redact the source audio.
