Run from this directory with Python 3 and `ASSEMBLYAI_API_KEY` in your environment:

```sh
./run.sh call.wav out.json
```

No packages need installing. Success exits 0 and atomically writes exactly
`transcript_id`, `text`, and `utterances` (each with `speaker` and `text`). Errors
exit non-zero without replacing the output. Diagnostics go to stderr and never
include transcript text or API error bodies. Polling stops after two hours;
submitted transcript IDs are printed to stderr for review even if polling fails.

The transcription request enables AssemblyAI's PII redaction for `person_name`,
`phone_number`, and `us_social_security_number`, with entity-name replacements.
It explicitly disables returning unredacted fields. Both local text and
utterances come directly from the redacted server response. No transcript is
deleted, including on failure: your retention job controls deletion.

Speaker diarization expects two people and returns labels such as A and B.
These labels distinguish voices; they do not identify agent/customer roles.
The runner uses Universal-2 with US English transcription.

AssemblyAI's automated PII detection can miss entities; these settings cannot
guarantee perfect removal on every recording. Review redacted results before
approving storage under a strict no-PII policy. Input audio is uploaded unchanged;
this runner requests transcript redaction, not audio redaction.

Validation: `python3 -m unittest -v test_transcribe.py`. The live sample result is
in `sample-out.json`; its transcript remains in AssemblyAI for your review and
retention job.
