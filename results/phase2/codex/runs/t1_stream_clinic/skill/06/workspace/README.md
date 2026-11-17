Run from this directory with the API key exported in your shell:

```sh
./run.sh <audio.wav> <out.json>
```

Input must be 16 kHz mono 16-bit PCM WAV. The installed `.venv` is used directly;
no activation or shell configuration is needed. To recreate it elsewhere, run
`python3 -m venv .venv` then `.venv/bin/pip install -r requirements.txt`.

The runner sends 50 ms PCM chunks at real-time speed to AssemblyAI's v3 streaming
API using Universal-3.6 Pro, Medical Mode (`medical-v1`), and speaker diarization.
It enables PII redaction for all supported policies with entity-name placeholders
and explicitly disables partial turns: streaming redaction applies only to finals.
It verifies the required features in `Begin.configuration` before sending audio.
The silence thresholds allow pauses inside medical terms and personal identifiers.
These transcription features incur AssemblyAI's applicable add-on charges.

`<out.json>` contains `{"turns": [{"speaker": "A", "text": "..."}]}` in turn order.
Speaker revisions are applied in receipt order through `Termination`; the last
revision wins. Unattributed speakers appear as `UNKNOWN`. Repeated final updates
replace a turn, and consecutive turns by the same speaker remain separate.

`<out.json>.frames.jsonl` contains every received application JSON message verbatim,
in receipt order, with a newline appended to each payload. It includes session,
turn, revision, and termination messages, including fields unknown to the code.
The raw audit is never regenerated from parsed or typed events. WebSocket control
frames (ping/pong/close) are transport messages, not JSON application messages.
No transcript text or API key is printed to the console.

Success exits 0 only after the server's `Termination` and an atomic output write.
Failures exit nonzero and preserve whatever audit messages were received. A stale
output is removed when a valid run starts. Output and audit paths are overwritten
on reruns. New files are created with owner-only access. The input WAV is read
without creating another audio copy; its existing sensitive audio is not redacted.
Streaming redaction relies on AssemblyAI's detection and cannot guarantee that
all personal details are detected. The runner uses the default edge-routed host.

Local protocol tests (no network service or API key required):

```sh
.venv/bin/python -m unittest -v test_transcribe.py
```
