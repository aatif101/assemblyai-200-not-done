Run from this directory:

```sh
export ASSEMBLYAI_API_KEY='your-key'
./run.sh sample.wav out.json
```

Requires Python 3.12+ and `websockets==17.4` (installed in this environment).
For another environment: `python3 -m pip install -r requirements.txt`.

The input must be uncompressed 16 kHz, mono, 16-bit PCM WAV. The runner sends
the PCM samples over AssemblyAI's v3 WebSocket in paced 100 ms chunks. It uses
`universal-3-6-pro`, `mode=max_accuracy`, `domain=medical-v1`, `speaker_labels=true`,
and server-side `redact_pii=true`. Audio capture for support is disabled.
It checks the Begin configuration before sending audio and fails on provider
warnings/errors, dropped connections, incomplete turns, or timeouts. There is
no fallback to an unredacted session or an asynchronous transcription API.

PII policies cover names, phone numbers, SSNs, email/address/location, birthdates,
dates, age, healthcare/account/financial identifiers, government IDs, credentials,
IP addresses, URLs, vehicle IDs, and number sequences. Substitution uses entity
names such as `[PERSON_NAME]`. Medications, conditions, and procedures remain
readable. See `PII_POLICIES` in `transcribe.py` for the precise list. AssemblyAI's
automated detection can miss identifiers; this does not guarantee that every
personal detail is removed. Confirm the provider's data-handling arrangements
before using real patient calls.

`out.json` contains final turns, ordered by `turn_order`, with formatted updates
replacing earlier versions and speaker revisions applied. Speaker labels are
anonymous voice IDs, not verified identities. A turn spanning multiple labeled
speakers is split in word order; an unavailable label is `unknown`.

`out.json.frames.jsonl` contains every received application message as the original
UTF-8 JSON payload plus a newline, including Begin, Turn, and Termination. It is
flushed as messages arrive. Redaction happens at AssemblyAI before these messages
are received; no local substitution changes the audit records. The log therefore
also inherits any missed redactions from the provider. Transcript contents and
credentials are never printed to the terminal.

Exit status is 0 only after server Termination and a complete final result.
Failures return nonzero and retain the frame log for diagnosis without publishing
a final JSON. Existing output/log paths are refused; choose a new output path for
each run. Files are created with owner-only permissions. The final JSON is
published atomically. The original input recording is neither copied nor deleted.
