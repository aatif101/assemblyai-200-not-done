Run from this directory:

```sh
./run.sh <audio.wav> <out.json>
```

Set `ASSEMBLYAI_API_KEY` in the shell environment. The input must be a nonempty
16 kHz mono 16-bit PCM WAV. Dependencies are already installed in `.venv`;
`run.sh` uses it directly, so activation is unnecessary in a fresh shell.
To recreate it, run `python -m venv .venv`, then
`.venv/bin/pip install -r requirements.txt`.

The runner streams raw PCM in paced 50 ms chunks to AssemblyAI v3 using
Universal-3.6 Pro, Medical Mode, speaker diarization, and all default PII
redaction policies with entity placeholders. It checks the required settings
in `Begin.configuration` before transmitting audio. Medical Mode supports
English, Spanish, German, and French. Medical Mode, diarization, and PII
redaction incur provider add-on charges.

PII redaction happens at AssemblyAI before final turns are delivered. Partial
turns are explicitly disabled because they are unredacted. Consequently live
results arrive at turn boundaries. The client writes every received JSON
message verbatim, in arrival order, to `<out.json>.frames.jsonl`, flushing each
message immediately. It does not print transcript text or server errors.
PII detection is probabilistic; it cannot guarantee every personal detail is
found. The audit log relies on the provider's redaction, including its word
fields and speaker-revision messages. Streaming does not redact source audio;
the runner does not copy or modify the input recording.

Final turns replace earlier versions with the same `turn_order`. Speaker
revisions are applied in arrival order, including those sent during shutdown.
The output is sorted by `turn_order`, retains separate turns even when they
share a speaker, and uses `unknown` for unavailable labels. Speaker IDs are
anonymous labels, not inferred patient/staff roles.

Success requires all audio to be sent and a server `Termination` message.
The final JSON is published atomically only on success. The process exits
nonzero on invalid input, missing credentials, API errors, timeout, premature
socket closure, or output failure; a failed session can leave a partial audit
log for diagnosis. Files are created with owner-only permissions. A final
chunk shorter than 50 ms is padded with silence. The default endpoint can
process audio in either the US or EU.

Local protocol tests:

```sh
.venv/bin/python -m unittest -v test_transcribe.py
```
