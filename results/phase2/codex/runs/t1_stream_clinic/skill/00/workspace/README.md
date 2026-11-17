# Clinic call transcription

Run from this directory with `ASSEMBLYAI_API_KEY` exported:

```sh
./run.sh sample.wav out.json
```

The runner uses the installed `.venv` directly, so a fresh shell requires no
activation. Input must be uncompressed 16 kHz mono 16-bit PCM WAV. Audio is sent
as 50 ms binary PCM chunks at wall-clock speed, with the last chunk padded with
silence if needed. Network delays slow playback rather than causing bursts.

`out.json` contains `{"turns": [{"speaker": "A", "text": "..."}]}`. Final
turns are ordered by `turn_order`; repeated finals replace the earlier version.
Speaker revisions are applied in arrival order through the server's final
`Termination` message. Speaker labels identify voices, not their clinical roles;
an unresolved label such as `PENDING` is retained as supplied by the service.

`out.json.frames.jsonl` contains every received application message, preserving
the original JSON payload and arrival order, with one newline appended per
message. It includes session, turn, revision, heartbeat, and error messages.
WebSocket transport control frames such as ping/pong aren't application messages.
The audit log is flushed after each message and remains available on failure.
The final output is published atomically only after successful termination.
Exit status is zero on success and nonzero on failure.

The connection explicitly selects `universal-3-6-pro`, enables Medical Mode
(`medical-v1`) and speaker diarization, and enables server-side PII redaction
with all default policies and `[ENTITY_TYPE]` replacements. Partial transcripts
are explicitly disabled: AssemblyAI only redacts finalized turns. This delivers
live finalized turns rather than word-by-word partials. The runner checks the
server's confirmed model, medical domain, diarization and redaction settings
before sending any audio. Longer silence thresholds help avoid splitting
dictated identifiers across turns. Medical Mode supports English, Spanish,
German and French. These features incur AssemblyAI usage charges.

No additional audio copy is made. The original input WAV remains on disk and
is not redacted by the streaming API. Automatic PII detection can miss details;
this integration cannot guarantee that every personal detail is removed. The
audit log is deliberately unmodified, so any unexpected sensitive content
returned by the provider would also appear there. New output files are created
with owner-only permissions. Before clinical use, validate redaction on your
call patterns and establish the appropriate AssemblyAI account agreement and
retention settings.

To recreate the local environment if it is removed:

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Run protocol tests with `.venv/bin/python -m unittest -v`.
