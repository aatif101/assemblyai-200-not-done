Run from this directory with the API key set in the shell:

```sh
./run.sh sample.wav out.json
```

Requires Python 3.12+ and `websockets==17.4` (already installed in this environment).
For another environment: `python3 -m pip install -r requirements.txt`.

Input must be an uncompressed 16 kHz mono 16-bit PCM WAV. The WAV header is
removed and PCM audio is sent in 100 ms packets paced with a monotonic clock.
One second of paced silence follows the recording to finish the last turn.

The AssemblyAI v3 streaming connection requests `u3-rt-pro`, `mode=max_accuracy`,
`domain=medical-v1`, speaker labels, formatted turns, and server-side PII
redaction. AssemblyAI may resolve the model alias to a newer model; its Begin
message records the actual model. Required medical, speaker, and redaction
settings must be confirmed by the server before any audio is sent.

The explicit redaction policies in `transcribe.py` cover names, phone numbers,
SSNs, email and street addresses, dates, ages, account and identity numbers,
financial credentials, and other identifying strings. Medications, conditions,
and procedures remain readable. Partial turns and support audio capture are
disabled. Redaction is automated detection and cannot guarantee that every
identifier will be recognized; validate it against your clinic's recordings
before production use. The existing input WAV itself contains the original
audio; this program does not make an additional recording.

`out.json` contains final turns ordered by `turn_order`. Formatted updates
replace previous versions; speaker revisions update earlier labels. Speaker
labels identify voices, not real-world identities; unavailable labels become
`Unknown`.

`out.json.frames.jsonl` contains every received application message, including
Begin, Turn, Warning, Error, and Termination, with the original JSON payload
preserved and a newline appended. It is flushed after each message. This audit
is deliberately not rewritten or locally redacted: privacy depends on the
requested server-side redaction, including for the audit. No transcript text or
credentials are printed to the terminal.

Output files have owner-only permissions. Both paths are replaced on a new run;
the final transcript is written atomically only after successful Termination.
Failures exit nonzero and leave the received frames available for diagnosis.
No retries reconnect a partially streamed call, avoiding duplicate turns.

Validation: `python3 -m unittest -v test_transcribe.py`.
