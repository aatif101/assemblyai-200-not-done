Run from this directory with the API key exported in your shell:

```sh
./run.sh sample.wav out.json
```

The installed `.venv` is used automatically; shell activation is unnecessary.
To recreate it: `python3 -m venv .venv`, then
`.venv/bin/pip install -r requirements.txt`.

Input must be uncompressed mono 16-bit PCM WAV (8–96 kHz). Audio samples are
sent in 50 ms chunks at real-time speed; the WAV header is not sent.

The AssemblyAI v3 WebSocket session requests Universal-3.6 Pro, Medical Mode
(`medical-v1`), speaker diarization, and server-side PII redaction. It confirms
these features in `Begin.configuration` before sending audio. Identity, contact,
location, date and financial policies are listed in `transcribe.py`; medical
conditions, medications and procedures remain visible. Partials are explicitly
disabled because only final turns receive server-side redaction. Final turns
arrive throughout the call, with a short delay for turn detection and redaction.
The API requires the policies query parameter as a JSON-encoded array.

`out.json` contains `{"turns": [{"speaker": "A", "text": "..."}]}` in turn order.
Repeated finals replace earlier versions of that turn. All speaker revisions,
including revisions just before `Termination`, are applied; labels are anonymous
speaker IDs, not inferred staff/patient roles. Unattributed labels are retained
as `PENDING` or `UNKNOWN` rather than inventing a speaker.

`out.json.frames.jsonl` contains the original server JSON payloads in arrival
order, including unknown message types, without parsing/re-serialization or
local redaction. It is flushed after every message and retained on failure.
The transcript is published atomically only after successful termination.
Success exits 0; invalid input, API errors, disconnects and timeouts exit nonzero.
A failed run leaves any pre-existing transcript output unchanged; check the exit
status before using it. Newly created output files have owner-only permissions.

PII detection and speaker attribution are probabilistic. The raw audit log relies
on AssemblyAI's server-side redaction and cannot guarantee that missed entities
are absent. Source audio is read only and is not redacted by this streaming API.
Medical Mode supports English, Spanish, German and French. The requested features
include paid add-ons on your AssemblyAI account.

Run local protocol tests with `.venv/bin/python -m unittest -v test_transcribe.py`.
