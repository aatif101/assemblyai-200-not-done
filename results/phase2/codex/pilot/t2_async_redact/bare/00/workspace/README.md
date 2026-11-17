Run from this directory with `ASSEMBLYAI_API_KEY` exported:

```sh
./run.sh <audio.wav> <out.json>
```

Python 3 and `assemblyai==1.10.0` are installed in this environment. To recreate
the dependencies elsewhere, use `python3 -m pip install -r requirements.txt`.

The script uploads the local recording, requests two-speaker diarization and
server-side redaction of `person_name`, `phone_number`, and
`us_social_security_number`, and polls for completion (up to two hours).
It explicitly disables returning unredacted fields. It writes only
`transcript_id`, redacted `text`, and redacted `utterances` with `speaker` and
`text`. Speaker labels such as A and B identify distinct voices; they do not
automatically identify job roles. The example recording has agent A and customer B.

The output file is replaced atomically with owner-only permissions. Success
exits 0; argument, network, transcription, validation, or output errors exit
nonzero. Errors do not overwrite an existing output file. A submitted transcript
ID is printed to stderr for review/recovery, without printing call content or
the API key. Transcripts are never deleted, including on failure or interruption;
your retention job remains responsible for deletion.

`sample-output.json` is the successful sample result. Its retained AssemblyAI
transcript was fetched again to verify the same redacted text and the absence of
populated unredacted fields. Names on both sides, the SSN, and the phone number
were replaced with entity markers in text and utterances.

AssemblyAI's PII detection is model-based and can miss entities. These settings
apply redaction to the stored transcript at AssemblyAI, but do not guarantee
perfect detection for every recording. Review remains necessary for a strict
no-PII requirement. The script does not request a redacted audio copy.

Run local checks with `python3 -m unittest -v test_transcribe.py`.
