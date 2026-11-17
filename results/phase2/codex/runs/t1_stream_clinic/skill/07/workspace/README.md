Run from this directory with the API key in the environment:

```sh
./run.sh sample.wav out.json
```

Dependencies are already installed in `.venv`; the launcher uses that interpreter
in fresh shells. To recreate it: `python3 -m venv .venv` then
`.venv/bin/pip install -r requirements.txt`.

Input must be 16 kHz mono, 16-bit PCM WAV. Audio is read incrementally, stripped
of its WAV header, and sent in paced 50 ms chunks. A final short chunk is padded
with silence. No audio copy is saved.

The v3 streaming connection explicitly selects Universal-3.6 Pro, Medical Mode
(`medical-v1`), speaker diarization, and PII redaction with all default policies
and entity-name placeholders. Partials are explicitly disabled because only final
turns are redacted by AssemblyAI. Consequently, live results arrive at turn
boundaries rather than word by word. Medical Mode supports English, Spanish,
German, and French. Detected medical information may also be masked by the
all-PII policy. Redaction is model based and cannot guarantee detection of every
sensitive detail; this runner does not independently certify that output is free
of PII. Streaming redaction does not redact the original audio file.

The runner checks `Begin.configuration` before sending audio. It collects final
turns by `turn_order`, replaces duplicate turn updates, and applies speaker
revisions in arrival order, including those received just before `Termination`.
Labels identify voices (A, B, etc.), not clinic/patient roles. Unresolved labels
such as PENDING are preserved; absent labels become UNKNOWN.

`out.json` is published atomically only after successful termination:
`{"turns":[{"speaker":"A","text":"Hello [PERSON_NAME]."}]}`.
Every received AssemblyAI JSON message is saved verbatim, in receipt order, with
one appended newline to `out.json.frames.jsonl`. The audit file is flushed after
each message and is retained on failure. Neither console output nor additional
logs contain transcript text. Output files use private permissions. Use a new
output path each run; existing outputs/audits are never overwritten.

Exit status is 0 on success, nonzero on invalid input, missing credentials,
configuration mismatch, API/network errors, unexpected partials, or missing
termination. Failed calls are not automatically retried, avoiding duplicate
sessions and audit ambiguity. The default AssemblyAI endpoint routes to US or EU
infrastructure. Streaming, Medical Mode, diarization, and PII redaction are billed
by AssemblyAI.

Run focused protocol tests with `.venv/bin/python -m unittest -v test_transcribe`.
