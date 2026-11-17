Run from this directory with `ASSEMBLYAI_API_KEY` exported:

```sh
./run.sh sample.wav out.json
```

The installed `.venv` is used directly, including from a fresh shell. To recreate it, run `python3 -m venv .venv` and `.venv/bin/pip install -r requirements.txt`.

Input must be 16 kHz mono 16-bit PCM WAV. Audio is sent as paced 50 ms PCM chunks (without the WAV header); the final short chunk is silence-padded. Streaming uses Universal-3.6 Pro with Medical Mode and speaker diarization. Explicit silence thresholds allow pauses within identifiers; diarization overrides the API's `mode` presets. Medical Mode supports English, Spanish, German, and French.

Server-side PII redaction covers all default policies, with entity-name placeholders. Partial turns are explicitly disabled because AssemblyAI only redacts final turns. Thus live text arrives at turn completion rather than word by word. The program validates the server's applied configuration before sending audio. Automated PII detection can miss entities; this is not a guarantee that every personal detail is removed. Streaming does not redact the source audio, which this program reads without copying or changing.

`out.json` contains `{"turns": [{"speaker": "A", "text": "..."}]}` in turn order. Repeated turn messages replace prior text; speaker revisions are applied in receipt order through session termination. Unresolved labels remain `PENDING` or `UNKNOWN`; speaker labels are anonymous, not staff/patient identities. The final JSON is atomically replaced only on success.

`out.json.frames.jsonl` contains every received application-level WebSocket JSON message verbatim, one per line in receipt order, including Begin, turns, revisions, errors, and Termination. It is flushed as messages arrive and retained on failure. Audit logging does not reserialize or locally redact messages. Consequently, its privacy protection depends on AssemblyAI's server-side redaction, including any word arrays and revision payloads. No transcript text or API key is printed to the terminal. New files are private to the current user. Existing output files are replaced on another run; use a distinct output path per call for audit retention.

Success exits 0; validation, connection, service, incomplete-session, or write failures exit non-zero. On failure a previous final output may still exist; always check the exit status. The endpoint uses AssemblyAI's default region routing. Medical Mode, diarization, and PII redaction are billed add-ons.

Tests: `.venv/bin/python -m unittest -v`.
