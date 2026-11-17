Run from this directory:

```sh
export ASSEMBLYAI_API_KEY='your-key'
./run.sh sample.wav out.json
```

Requires Python 3; no packages need installing. The key must be exported in the
shell where you run the command. Success exits 0 and atomically writes only
`transcript_id`, `text`, and `utterances` (each with `speaker` and `text`). Errors
exit nonzero and leave any existing output unchanged. The transcript ID is also
printed to stderr once submitted, including when processing later fails.

The request enables AssemblyAI's server-side PII redaction for `person_name`,
`phone_number`, and `us_social_security_number`, using entity placeholders.
Redaction is requested before transcription so AssemblyAI's retained transcript
and the local output use the redacted result. The program never requests deletion
of a transcript; your retention job can use `transcript_id` after review.
No raw response or transcript text is logged or saved separately.

Speaker diarization expects two people. Speaker labels such as A and B distinguish
voices within a recording; they do not identify agent/customer roles, and labels
can differ between recordings.

PII detection and speaker diarization are probabilistic. Server-side redaction
cannot guarantee every spoken name or number will be detected. If zero missed
PII is a strict requirement, automated transcription alone cannot certify that
requirement; validate accuracy against your calls and review the redacted output.
The original audio is uploaded to AssemblyAI; this command does not request a
redacted audio copy.
