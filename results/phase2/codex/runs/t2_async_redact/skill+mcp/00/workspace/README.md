Run from a fresh shell with `ASSEMBLYAI_API_KEY` exported:

```sh
./run.sh <audio.wav> <out.json>
```

Requires Python 3.12+ (already installed); no packages or shell activation needed.
Success returns 0 and atomically writes exactly `transcript_id`, `text`, and
`utterances` (each with `speaker` and `text`). Failures return non-zero and leave
any existing output untouched. Diagnostics go to stderr without transcript text
or API response bodies. The transcript ID is logged after submission so failed
jobs can also be reviewed.

The asynchronous AssemblyAI API receives the raw audio and performs PII redaction
for `person_name`, `phone_number`, and `us_social_security_number` before returning
the transcript. Unredacted output is explicitly disabled. Both full text and
utterances come from the redacted server response; no additional analysis
features are enabled. The server transcript is retained for your review and
retention job. The runner never deletes transcripts or changes retention settings.

Two-speaker diarization uses `speakers_expected: 2`. Labels A/B distinguish voices;
they do not automatically assign agent/customer roles. Redaction and diarization
are model predictions and can make mistakes; automated redaction cannot guarantee
that every sensitive value is detected. Review is still required for an absolute
no-PII storage requirement. This script does not redact or alter the source audio.

`sample-output.json` is the successful sample result. Tests: `python3 -m unittest -v`.
