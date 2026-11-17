Run from this directory with Python 3 and your API key in the environment:

```sh
export ASSEMBLYAI_API_KEY='your-key'
./run.sh <audio.wav> <out.json>
```

No packages need installing. The script uploads the recording, requests two-speaker
diarization and server-side redaction of `person_name`, `phone_number`, and
`us_social_security_number`, then polls for completion. Unredacted response fields
are explicitly disabled. It writes only `transcript_id`, `text`, and utterances
containing `speaker` and `text`, using an atomic file replacement. It exits 0 on
success and nonzero on failure; errors leave any existing output file intact.

Speaker labels such as `A` and `B` distinguish voices; they do not identify which
speaker is the agent. Redaction applies to both speakers. Like transcription,
automated PII detection can miss information; it cannot guarantee that every PII
mention is detected.

The script never deletes an AssemblyAI transcript, including on failure or
interruption. The submitted transcript ID is printed to stderr so your retention
job can find it even if processing or local output fails. Polling stops after one
hour; the remote job can continue. API response bodies and transcript contents are
never logged. Uploaded audio is sent to AssemblyAI with its original speech;
this script requests transcript redaction, not audio redaction.

The live sample test completed successfully with two speakers, four name
redactions, one SSN redaction, and one phone redaction in both the full text and
utterances. A subsequent GET confirmed AssemblyAI returned the same redacted
content. That test transcript remains in AssemblyAI for retention review:
`8e6260ca-ae08-43a6-b067-a82cab186444`.
