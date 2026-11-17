Run from this directory with Python 3.10+:

```sh
export ASSEMBLYAI_API_KEY='your-key'
./run.sh sample.wav out.json
```

The dependency is installed in this environment. To install it elsewhere:
`python3 -m pip install -r requirements.txt`.

Input must be an uncompressed 16 kHz mono, 16-bit PCM WAV. The client sends
100 ms chunks of audio (without the WAV header) at real-time speed over
AssemblyAI's v3 WebSocket API. A 50-second recording takes at least 50 seconds.
Sending and receiving run concurrently.

The session requests `u3-rt-pro`, `domain=medical-v1`, and `mode=max_accuracy`,
with speaker diarization and server-side streaming PII redaction. Personal
identifiers are replaced with entity labels such as `[PERSON_NAME]` and
`[PHONE_NUMBER]`. The policies cover names, phone numbers, SSNs, contact and
address details, dates of birth, ages, financial and healthcare identifiers,
credentials, and other identifying numbers. Medication names, conditions,
and procedures remain in the clinical transcript. The exact policies and
request parameters are in `transcribe.py`.

Partial turns are disabled so the client receives finalized, redacted text
rather than unredacted intermediate hypotheses. The client requires the
session's Begin configuration to confirm medical mode, speaker labeling,
and redaction before sending audio. It does not enable support audio capture.
It fails on any server warning instead of silently accepting degraded settings.
Server-side PII detection can miss identifiers; this is automated masking,
not a guarantee that every possible personal detail will be detected.

`out.json` contains `{"turns": [{"speaker": "A", "text": "..."}]}`. Completed
turns are ordered by `turn_order`, repeated final/formatting updates replace
earlier versions, and speaker revisions are applied. An unavailable speaker
label is represented by `UNKNOWN`. Speaker letters identify voices, not roles
such as clinician or patient. The file is written atomically only after the
server's Termination message and successful completion of audio sending.

`out.json.frames.jsonl` contains every received JSON message, including Begin,
Turn, errors/warnings, and Termination, with its original bytes and order
preserved. Each message gets a newline separator. Redaction happens at
AssemblyAI before delivery; the audit log is never edited locally. Both output
files use owner-only permissions. A failed run can leave a partial audit log;
it does not replace a pre-existing final transcript. Standard error contains
status and safe diagnostics, never transcripts or the API key.

Exit status is 0 only on successful completion, 1 on failure, 2 for invalid CLI
arguments, and 130 on interruption. There is no automatic replay/retry that
could duplicate turns. Connection and response waits have finite timeouts.

Offline verification: `python3 -m unittest -v test_transcribe.py`.
