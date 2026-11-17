Run from this directory with the API key in your environment:

```sh
./run.sh <audio.wav> <out.json>
```

The installed `.venv` is used automatically, including in a fresh shell. To rebuild it:

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Input must be uncompressed 16 kHz mono signed 16-bit PCM WAV. The runner sends
50 ms PCM chunks at wall-clock speed, padding the last short chunk with silence.
It uses AssemblyAI streaming v3 with `universal-3-6-pro`, `domain=medical-v1`,
speaker diarization, all PII policies, and entity-name replacements. It verifies
the server's applied configuration before sending audio. Medical Mode supports
English, Spanish, German, and French.

PII redaction is performed by AssemblyAI before final turns are delivered.
Partial turns are explicitly disabled because they are not redacted. This means
live transcript updates arrive as finalized turns rather than word-by-word
partials. Automated PII detection can miss sensitive information; this code
cannot guarantee that every personal detail will be detected. It does not create
an audio copy or redact the original input recording.

`<out.json>.frames.jsonl` retains each received JSON payload verbatim, in arrival
order, followed by a newline. Unknown message fields are preserved. The audit is
flushed on every message, including errors; it may be incomplete on failure.
It is never rewritten to apply speaker revisions or perform local redaction.
Both output files are created with owner-only permissions.

The final output has the requested `{"turns":[{"speaker":"A","text":"..."}]}`
shape. Final turns replace earlier versions with the same turn order. Speaker
revisions are applied in arrival order, including those before `Termination`.
Turns remain separate even when consecutive turns share a speaker. Unattributed
labels (`PENDING`/`UNKNOWN`) are preserved rather than guessed. The final JSON is
atomically replaced only after successful termination; an existing output is
left untouched on failure. Exit status is zero on success and nonzero on failure.
Network failures are not retried automatically to avoid duplicating audio/turns.

Validation:

```sh
.venv/bin/python -m unittest -v test_transcribe.py
./run.sh sample.wav sample-output.json
```
