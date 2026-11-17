Run from this directory with `ASSEMBLYAI_API_KEY` exported:

```sh
./run.sh sample.wav out.json
```

The installed `.venv` is used directly, so shell activation is unnecessary.
To recreate it: `python3 -m venv .venv` then
`.venv/bin/pip install -r requirements.txt`.

Input must be uncompressed 16-bit PCM, 16 kHz mono WAV. The client strips
the WAV header and sends 50 ms binary audio packets at real-time speed;
the final packet is padded with silence if shorter than 50 ms.

The client explicitly requests Universal-3.6 Pro, Medical Mode (`medical-v1`),
speaker diarization, and PII redaction with all supported policies and
`[ENTITY_TYPE]` placeholders. It verifies the model and required features in
`Begin.configuration` before sending audio. Medical Mode supports English,
Spanish, German, and French. Medical Mode, diarization, and PII redaction
incur AssemblyAI add-on charges.

Only server-redacted final turns are requested: partial turns are explicitly
disabled because AssemblyAI does not redact partials. Live results therefore
arrive at turn completion. Explicit silence thresholds allow pauses within
medical phrases and identifiers; diarization otherwise overrides mode presets.
All supported PII categories are redacted, which may also mask medical details
covered by those policies. Detection is model-based and can miss PII; this
implementation cannot guarantee that every personal detail is recognized.
Streaming redaction applies to transcripts, not the source audio.

`out.json` contains `{"turns":[{"speaker":"A","text":"..."}]}` in
`turn_order` order. Final turns replace earlier versions of the same turn.
Speaker revisions are applied in arrival order, including revisions emitted
at session end. Speaker labels denote voices, not verified identities;
`PENDING` or `UNKNOWN` can remain when attribution is unavailable.

`out.json.frames.jsonl` contains every received application JSON message,
including Begin, Turn, SpeakerRevision, and Termination, exactly as received
(UTF-8, with a newline appended), without parsing and reserializing it.
It is flushed after every message. WebSocket ping/pong control frames are
handled by the transport and are not JSON application messages.
The audit log is kept on failure as well. It is never locally sanitized,
so its privacy depends on AssemblyAI honoring the requested redaction for
all message fields. Error payloads are not echoed to the console.

Exit status is zero only after all audio is sent, Termination is received,
and the final JSON is written atomically. Failure returns nonzero and does
not replace any pre-existing final JSON. Each invocation replaces its audit
file, so use a distinct output path per call. Output files have mode 0600.
No audio copies or unredacted transcript files are created by the client.
The default endpoint may process in the US or EU; the endpoint is defined in
`transcribe.py` if deployment requires a specific AssemblyAI data zone.

Run local protocol tests with `.venv/bin/python -m unittest -v`.
