Run from this directory with the key exported in your shell:

```bash
export ASSEMBLYAI_API_KEY='your-key'
./run.sh sample.wav out.json
```

The installed `.venv` is used directly; no activation is needed. To recreate it:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

The runner streams mono, 16-bit PCM WAV audio (8–96 kHz) as binary audio in
50 ms chunks, paced at the recording's sample rate. WAV headers are excluded.
It receives messages concurrently with audio transmission. It uses the v3
WebSocket API with `universal-3-6-pro`, `domain=medical-v1`, speaker diarization,
and server-side PII redaction with all policies and entity-name replacements.
Partials are explicitly disabled because AssemblyAI only redacts final turns.
Medical Mode supports English, Spanish, German and French.

`out.json` contains `{"turns": [{"speaker": "A", "text": "..."}]}` in turn
order. Final messages replace earlier versions of the same turn. SpeakerRevision
messages update the labels; the last correction wins. Speaker labels identify
voices rather than clinical roles. Unresolved labels may remain `PENDING` or
`UNKNOWN`. Each server turn is retained, including consecutive turns from the
same speaker.

`out.json.frames.jsonl` records every received JSON message verbatim, in arrival
order, with a newline appended after each message. It includes Begin,
SpeechStarted, Turn, SpeakerRevision, Termination and any other server events.
The audit file is flushed for every message. Messages are never printed to the
console. New output files have owner-only permissions.

The runner checks the server's Begin configuration before sending audio. It
sends Terminate after the complete recording and waits for Termination and
speaker corrections before atomically publishing the transcript. Success exits
0; invalid input, missing keys, connection errors, API errors, interrupted
sessions and shutdown timeouts exit nonzero. An unsuccessful run can leave a
partial audit log; an existing transcript is replaced only on success.

Redaction is AssemblyAI's automated detection, so it cannot guarantee that every
identifier will be detected. The original input audio is untouched and still
contains the caller's details; streaming text redaction does not redact audio.
See [streaming PII redaction](https://assemblyai.com/docs/streaming/pii-redaction)
and [Medical Mode](https://assemblyai.com/docs/streaming/medical-mode).

Run local protocol checks with `.venv/bin/python -m unittest -v`.
