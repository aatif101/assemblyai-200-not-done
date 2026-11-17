Run from a fresh shell (dependencies are installed in `.venv`):

```sh
./run.sh sample.wav out.json
```

`ASSEMBLYAI_API_KEY` must be set. Input is 16 kHz mono, 16-bit PCM WAV.
The runner sends raw PCM in paced 50 ms chunks, padding the final chunk with
silence if needed. It receives messages concurrently and waits for server
termination before publishing `out.json`. Failures return nonzero; the audit
log retains messages received before failure. An existing output JSON is
replaced only after a successful session.

Uses Universal-3.6 Pro, Medical Mode (`medical-v1`), speaker diarization,
and all supported PII redaction policies with `[ENTITY_TYPE]` placeholders.
Partials are explicitly disabled because AssemblyAI redacts only final turns.
Before sending audio, the runner checks the server's Begin configuration for
the requested model, Medical Mode, diarization, and PII redaction.
It applies incremental speaker revisions in arrival order and writes one
entry per final turn, ordered by `turn_order`. Unassigned labels use `UNKNOWN`;
server labels such as `PENDING` are retained if never revised.

`out.json.frames.jsonl` contains each server JSON payload exactly as received,
followed by a newline, in arrival order. It includes Begin, final turns,
heartbeats, revisions, errors, and Termination. No transcript text or server
error details are printed. Both output files are created with owner-only
permissions. The WAV file itself is not redacted or copied. Streaming PII
redaction is detection-based and cannot guarantee that every personal detail
will be recognized; the audit log preserves exactly what the service sends.

To recreate dependencies if needed:

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Run offline protocol checks with `.venv/bin/python -m unittest -v test_transcribe.py`.
