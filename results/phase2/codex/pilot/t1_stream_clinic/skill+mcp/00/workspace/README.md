Run from this directory with an API key in the environment:

```sh
./run.sh sample.wav out.json
```

The installed `.venv` is used automatically, including in a fresh shell. Input
must be uncompressed 16-bit PCM WAV, 16 kHz, mono. No WAV header is sent.
Audio is paced in 100 ms chunks; a final short chunk is padded with silence
to satisfy the API's minimum chunk length.

`out.json` contains `{"turns": [{"speaker": "A", "text": "..."}]}`.
Only finalized turns are included, ordered by `turn_order`, with duplicate
updates replaced and speaker revision deltas applied in arrival order.
The text comes directly from each final turn's `transcript`; it isn't rebuilt
from word-level text. Labels identify voices, not clinical roles. The API may
leave short or ambiguous turns labeled `PENDING` (or without an attribution,
represented here as `UNKNOWN`).

`out.json.frames.jsonl` preserves every received application JSON message,
including Begin, Turn, SpeakerRevision, and Termination, in reception order.
The original message bytes are written with a newline appended. The program
never prints transcript contents or the API key. Final output is atomically
published only after Termination; failure returns nonzero and retains the
audit messages received before failure. An existing output JSON is replaced
only on success, and the audit file is overwritten for each run.

The v3 WebSocket uses Universal-3.6 Pro with Medical Mode (`medical-v1`),
speaker diarization, all supported PII redaction policies, and entity-name
replacement tokens. Partials are explicitly disabled: AssemblyAI redacts only
finals, so enabling partials would expose personal details in the raw audit log.
Required settings are checked in Begin before patient audio is sent.
Medical silence thresholds are 800/3600 ms; diarization overrides mode presets.
Final speaker corrections are processed before the result is saved.

Redaction is automated detection, so it cannot guarantee every personal detail
will be caught. The raw audit log preserves exactly what the service sends;
it cannot both be unmodified and independently scrub missed PII. Streaming
redaction also does not redact the source audio file.

References:
- https://www.assemblyai.com/docs/streaming/medical-mode
- https://www.assemblyai.com/docs/streaming/pii-redaction
- https://www.assemblyai.com/docs/streaming/label-speakers-and-separate-channels

Run local checks with `.venv/bin/python -m unittest -v`.
