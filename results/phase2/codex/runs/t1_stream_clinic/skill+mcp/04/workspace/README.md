Run from this directory with your API key exported:

```sh
./run.sh <audio.wav> <out.json>
```

Accepts uncompressed 16 kHz mono 16-bit PCM WAV. The installed `.venv` is
used directly, so a fresh shell needs no activation. To recreate dependencies:

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

The runner sends raw PCM in 50 ms chunks at real-time speed (a final short
chunk is padded with silence). It uses Universal-3.6 Pro, Medical Mode,
speaker diarization, and all supported PII redaction policies. Partial turns
are explicitly disabled because they are not redacted. Masked final turns
arrive during the call; word-by-word partial output is intentionally unavailable.
Medical Mode supports English, Spanish, German, and French.

Before sending audio, the runner checks the server's Begin configuration for
the requested model, Medical Mode, diarization, and redaction. It waits for
Termination and applies SpeakerRevision deltas in arrival order. Final output
contains one entry per final turn, sorted by turn_order, with the full final
transcript and corrected turn speaker. Unattributed labels such as PENDING may
remain if the service cannot identify a speaker; labels do not identify clinic
staff or patients by role.

`<out.json>.frames.jsonl` contains each server JSON message's original text
payload, followed by a newline, in receive order, including Begin, revisions,
and Termination. No fields are filtered or rewritten. It is flushed after every
message. WebSocket transport control frames are not application JSON messages.
Both output files have owner-only permissions. Final JSON is replaced atomically
only after a successful session; failures exit nonzero and may leave a partial
audit log. A pre-existing final JSON is retained on failure, so always check exit
status before consuming it. Transcript content and keys are not printed.

PII detection is probabilistic: enabling redaction cannot guarantee that every
personal detail will be detected. The audit log necessarily preserves whatever
the service sends, as requested. Streaming redaction does not mask the source
audio; this program does not create an audio copy. Medical Mode, diarization,
and redaction incur AssemblyAI add-on charges.

Documentation:
- https://www.assemblyai.com/docs/streaming/pii-redaction
- https://www.assemblyai.com/docs/streaming/medical-mode
- https://www.assemblyai.com/docs/streaming/label-speakers-and-separate-channels

Run protocol/pacing/failure tests with:

```sh
.venv/bin/python -m unittest -v test_transcribe.py
```
