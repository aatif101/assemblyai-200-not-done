Run from this directory with Python 3 and your API key exported:

```bash
export ASSEMBLYAI_API_KEY='your-key'
./run.sh <audio.wav> <out.json>
```

No third-party packages or shell activation are needed. Success exits 0 and
atomically writes the requested JSON, with file permissions 0600. Failures exit
non-zero, leave any existing output untouched, and print an error to stderr.
The submitted transcript ID is printed to stderr so you can review or manage a
job even if polling or local output fails. Polling stops after 24 hours.

The async AssemblyAI job enables speaker diarization with exactly two expected
speakers, plus server-side PII redaction for `person_name`, `phone_number`, and
`us_social_security_number`. It explicitly disables returning unredacted data.
Only the returned redacted text and speaker utterances are saved. Additional
analysis features that could expose PII are not requested. The runner fetches
the persisted transcript and verifies its redaction settings before writing.

Speaker labels such as A and B distinguish voices within a call; they do not
identify which voice is the agent or customer. PII detection and diarization
are probabilistic: enabling these settings cannot guarantee that every spoken
identifier is detected. Review redaction quality before relying on transcripts
for a strict no-PII storage requirement.

The runner never deletes transcripts or sets a deletion TTL. Your retention job
remains responsible for deletion after review. This workflow redacts transcript
data; it uploads the original audio and does not request audio redaction.

The sample was successfully tested with `./run.sh sample.wav sample.out.json`.
Its output contains two speaker labels and redaction markers for names, a phone
number, and an SSN. That test transcript remains in AssemblyAI.
