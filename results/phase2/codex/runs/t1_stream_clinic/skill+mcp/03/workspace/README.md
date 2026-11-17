Run from this directory with the API key exported:

```sh
./run.sh sample.wav out.json
```

The installed `.venv` is used directly, so no shell activation is required.
To recreate dependencies: `python3 -m venv .venv` then
`.venv/bin/pip install -r requirements.txt` (Python 3.11+).

Input must be uncompressed 16 kHz mono 16-bit PCM WAV. Audio is sent as raw
PCM in 50 ms chunks at wall-clock speed; a short final chunk is padded with
silence. The runner uses Universal-3.6 Pro with Medical Mode and speaker
diarization. It verifies the applied settings in the server's Begin message
before sending audio.

PII redaction is enabled with all policies and entity-name placeholders.
Partial turns are explicitly disabled because only final turns are redacted
by AssemblyAI. All policies includes clinical entities such as drugs,
conditions, and procedures, so these may also appear as placeholders.
Redaction is model-based and cannot guarantee detection of every sensitive
detail. The runner does not create audio copies; the supplied WAV remains
the caller's responsibility.

`out.json` contains one entry per final turn, sorted by turn order, with the
latest speaker revisions applied. Speaker labels are anonymous letters;
`PENDING` or `UNKNOWN` indicates an unattributed speaker. Turn boundaries
do not necessarily indicate speaker changes.

`out.json.frames.jsonl` contains every received WebSocket JSON payload in
arrival order, without parsing/reserializing its contents. No client-generated
messages or metadata are inserted. Each payload is followed by a newline.
The audit file is flushed as messages arrive, including errors. The final
output is replaced atomically only after a successful Termination message.
Failures return nonzero and may leave an incomplete audit log; an existing
final output is retained. No transcript text or API key is printed to the terminal.

Run checks with `.venv/bin/python -m unittest -v test_transcribe.py`.

Protocol references:
- https://www.assemblyai.com/docs/streaming/pii-redaction
- https://www.assemblyai.com/docs/streaming/medical-mode
- https://www.assemblyai.com/docs/streaming/label-speakers-and-separate-channels
