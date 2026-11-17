Run from this directory with `ASSEMBLYAI_API_KEY` set:

```sh
./run.sh audio.wav out.json
```

Requires Python 3.12+ and `websockets` (installed in this environment). To set
up another environment: `python3 -m pip install -r requirements.txt`.

Input must be uncompressed 16-bit PCM, 16 kHz mono WAV. Audio is sent as binary
PCM in 100 ms chunks, paced by the monotonic clock. The last short chunk is
padded to at least 50 ms. The program receives messages concurrently, sends
`Terminate` after the audio, and waits for the server's termination response.

AssemblyAI v3 uses `universal-streaming-english` with `domain=medical-v1`,
`speaker_labels=true`, and `redact_pii=true`. Redaction is performed by the
server before its responses are stored. Partial turns are disabled. The session
must confirm the medical domain, speaker labels, and redaction before audio is
sent. Support audio capture is disabled. Identifier policies are listed in
`PII_POLICIES` in `transcribe.py`; medications, conditions, and procedures remain
readable. Redaction and speaker identification are probabilistic service
features, so this does not guarantee detection of every identifier or establish
clinical/compliance suitability.

`out.json` contains `{"turns": [{"speaker": "A", "text": "..."}]}`.
Final turns are ordered by `turn_order`; formatted updates replace earlier
versions, and any server speaker revisions are applied. Empty turns are omitted.
If the server provides no speaker label, the label is `Unknown`.

`out.json.frames.jsonl` contains every received WebSocket JSON payload verbatim,
in arrival order, with a newline appended to each. This includes session,
transcript, warning, error, and termination messages. No SDK normalization or
local redaction is applied to this audit log. Both output files are created with
owner-only permissions. Transcript text and server payloads are never printed
to the console. The source audio is read in place.

Exit status is zero only after successful completion and publication of the final
JSON. Failures exit nonzero and retain any received audit messages for diagnosis.
Existing output/audit paths are refused to protect previous records; choose a
new output path for each run. A completed JSON is published atomically.

Tests: `python3 -m unittest -v test_transcribe.py`.
