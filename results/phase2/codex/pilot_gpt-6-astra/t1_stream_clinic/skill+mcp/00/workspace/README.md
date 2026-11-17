Run from this directory with the API key exported:

```bash
./run.sh sample.wav out.json
```

The installed `.venv` is used automatically in a fresh shell. To recreate it:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Input must be 16 kHz, mono, 16-bit PCM WAV. Audio is sent as paced 50 ms
binary chunks over AssemblyAI's v3 WebSocket. The last short chunk is padded
with silence. No audio copy is created.

The configuration selects Universal-3.6 Pro, Medical Mode (`medical-v1`),
speaker diarization, all PII redaction categories, and entity-name masks.
Partial turns are explicitly disabled: AssemblyAI redacts only final turns,
so results arrive as turns finish, rather than word by word. Longer silence
thresholds reduce splitting of dictated personal details and medical phrases.
Medical Mode particularly improves medication names; recognition and PII
detection are probabilistic and cannot guarantee perfect accuracy or zero
missed personal details. All-category redaction may also mask clinical entities.

`out.json` contains exactly `{"turns":[{"speaker":"A","text":"..."}]}`.
Turns are ordered by `turn_order`; repeated finals replace the prior version.
SpeakerRevision deltas are applied through Termination. Unresolved labels are
kept as supplied (e.g. PENDING), with UNKNOWN for absent/null labels. Labels
identify voices, not verified patient/staff identities.

`out.json.frames.jsonl` receives every server JSON message in arrival order,
preserving its original payload and appending a newline. It includes Begin,
Turn, SpeakerRevision, Termination and any other received message. The log is
flushed after each message; transcript text is never printed to the console.
Redaction happens on the server, so the audit log is not locally rewritten.
Both files have owner-only permissions by default.

The program checks Begin.configuration before sending audio. Missing or
incorrect model, medical mode, diarization or redaction settings fail the run.
Success requires Termination and writes the output atomically, then exits 0.
Failures exit non-zero, retain any received audit frames, and do not publish a
final output. Existing output/audit paths are refused; use a new output name
for each run. Connection and shutdown waits are bounded.

Streaming text redaction does not redact the source WAV or configure provider
retention. The supplied WAV remains in place. Production handling of patient
audio requires appropriate AssemblyAI account data controls and a BAA.

Tests: `.venv/bin/python -m unittest -v test_transcribe.py`

API references:
- https://www.assemblyai.com/docs/streaming/medical-mode
- https://www.assemblyai.com/docs/streaming/pii-redaction
- https://www.assemblyai.com/docs/streaming/label-speakers-and-separate-channels
