Run from this directory with Python 3 and an exported API key:

```sh
export ASSEMBLYAI_API_KEY='your-key'
./run.sh audio.wav out.json
```

No dependencies need installing. The script exits 0 only after writing the JSON
successfully; errors exit nonzero. It writes exactly `transcript_id`, `text`, and
`utterances` (each with `speaker` and `text`). Output is replaced atomically and
created with permissions 0600. On failure, an existing output stays unchanged.

The audio is uploaded to AssemblyAI and transcribed in US English using
Universal-2, speaker labels, and two expected speakers. Labels such as `A` and `B`
distinguish voices within a call; they do not assign agent/customer roles.

PII redaction is enabled on the initial AssemblyAI request for `person_name`,
`phone_number`, and `us_social_security_number`, with entity-name placeholders.
Unredacted fields are explicitly disabled. Both transcript text and utterances
come from that server-redacted response. The script checks the returned privacy
settings before saving, never logs response bodies, and never deletes a remote
transcript. The transcript ID is printed to stderr after submission, including
when a later step fails, so your review/retention job can find it. Polling times
out after one hour; failed or interrupted runs leave submitted jobs in AssemblyAI.

Automated PII detection can miss information; these API settings cannot guarantee
that every sensitive utterance will be detected. Review and validation on your
actual call data are still needed for a strict no-PII requirement. This script
redacts transcripts; it does not redact the original recording.

The supplied sample was tested successfully. AssemblyAI's retained transcript
matched the local output, with two speaker labels and placeholders for four names,
one phone number, and one Social Security number in both text and utterances.
The sample transcript was left in AssemblyAI for your retention process.

Run local request/response contract and failure checks with:

```sh
python3 -m unittest -v test_transcribe.py
```
