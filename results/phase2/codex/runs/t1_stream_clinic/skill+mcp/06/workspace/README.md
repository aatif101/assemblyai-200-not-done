Run from this directory:

```bash
./run.sh <audio.wav> <out.json>
```

`ASSEMBLYAI_API_KEY` must be exported in your shell. The Python environment and
dependency are already installed in `.venv`; no shell activation is needed.
To recreate the environment: `python3 -m venv .venv`, then
`.venv/bin/pip install -r requirements.txt`.

Input must be uncompressed 16-bit PCM, 16 kHz, mono WAV. The program reads the
WAV header locally and sends only PCM in 50 ms packets, paced at real-time speed.
It uses AssemblyAI Streaming v3 with `universal-3-6-pro`, Medical Mode
(`domain=medical-v1`), speaker diarization, and server-side PII redaction across
all supported categories. It explicitly disables partial turns because those
are not redacted. Medical Mode supports English, Spanish, German, and French.

`out.json` contains final redacted turn text in `turn_order` order, with the last
speaker revisions applied. A turn gets its dominant speaker's label (A, B, etc.);
unresolved labels such as PENDING are preserved. Labels identify voices, not
clinical roles. Word arrays are never used to reconstruct text.

`out.json.frames.jsonl` contains every received JSON message, including Begin,
Turn, SpeakerRevision, and Termination, in receive order. The original message
payload is written unchanged with a newline appended, and flushed immediately.
No transcript content is printed to the console. No audio copies are written.
The existing source recording remains on disk.

Success exits 0 only after Termination and atomically publishes the final JSON.
Failures exit nonzero; the audit retains any messages received before failure.
An existing final output is replaced only on success, so always check the exit
code. The API key is sent in the Authorization header, never in the URL.

PII detection is probabilistic: server-side redaction cannot guarantee that every
identifier is caught. The unmodified audit necessarily relies on AssemblyAI's
redaction of all returned fields; local filtering would change that audit.
Streaming does not redact the source audio. A deployment requiring zero missed
PII needs a separately validated data-handling policy.

Verification: `.venv/bin/python -m unittest -v test_transcribe.py`.

References:
- https://assemblyai.com/docs/streaming/pii-redaction
- https://assemblyai.com/docs/streaming/label-speakers-and-separate-channels
