Run from a fresh shell with `ASSEMBLYAI_API_KEY` exported:

```sh
./run.sh sample.wav out.json
```

Python 3 and `websockets==17.4` are required. Dependencies are already installed
in this environment; to install elsewhere, run
`python3 -m pip install -r requirements.txt`.

The input must be a nonempty 16 kHz, mono, signed 16-bit PCM WAV. The runner
streams the PCM over AssemblyAI's v3 WebSocket in 100 ms packets paced by a
monotonic clock. It selects `u3-rt-pro` with `mode=max_accuracy`, the
`medical-v1` domain, speaker diarization, and service-side PII redaction.
It requires the server to confirm redaction, medical mode, and diarization
before sending any audio. Configuration warnings and API errors fail the run;
there is no fallback to an unredacted session.

PII policies cover names, phone and Social Security numbers, birth dates,
dates, ages, email and physical locations, account/healthcare/banking/card
identifiers, licenses, passports, IP addresses, passwords, usernames, URLs,
and vehicle identifiers. Medication names, conditions, and procedures remain
available as clinical vocabulary. Redactions use labels such as `[PERSON_NAME]`.
Speculative partial turns are disabled so only finalized text is requested.
Redaction is performed by AssemblyAI's recognition models and can miss details;
an unmodified audit log necessarily retains anything the service fails to mask.

On success, `out.json` contains:

```json
{"turns":[{"speaker":"A","text":"Hello, [PERSON_NAME]."}]}
```

Final updates replace earlier versions of the same API turn. Speaker revisions
are applied. A turn containing multiple speakers is split at word-level speaker
changes, preserving the final redacted text and its order. Labels identify voices,
not roles or verified identities; an unavailable label is `Unknown`.

`out.json.frames.jsonl` records every received JSON message, including session
metadata and any errors, in arrival order. Each payload is written verbatim with
one appended newline; fields and text are never filtered or reserialized.
Both files have private permissions. Transcript text is never printed to the
terminal. The transcript file is replaced atomically only after the service
acknowledges termination; the audit log is flushed as messages arrive and
remains available after failure. Existing audit logs at that path are overwritten.

Success exits 0; invalid input, missing credentials, API/network failures,
incomplete sessions, and write failures exit nonzero. On failure, any transcript
from a previous run at the output path is left untouched.

Run the local protocol and aggregation checks with:

```sh
python3 -m unittest -v test_transcribe.py
```
