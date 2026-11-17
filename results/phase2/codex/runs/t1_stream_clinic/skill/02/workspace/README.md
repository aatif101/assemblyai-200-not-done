Run from this directory with the existing environment variable:

```sh
./run.sh sample.wav out.json
```

Requires a 16 kHz, mono, 16-bit PCM WAV. The installed `.venv` is used directly,
so a fresh shell needs no activation. To recreate it if needed:

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

The runner streams raw PCM over AssemblyAI's v3 WebSocket at real-time speed
in 50 ms chunks. It uses Universal-3.6 Pro with `domain=medical-v1`, speaker
diarization, and PII redaction for all supported entity types, with entity-name
placeholders. It checks the server's Begin configuration before sending audio.
The silence settings allow longer pauses within spoken identifiers; diarization
otherwise overrides the model's accuracy/latency presets.

PII masking happens on AssemblyAI's servers for final turns. Partial turns are
explicitly disabled because they are unredacted. Consequently, live results
arrive when turns finalize, rather than word by word. Masking uses AssemblyAI's
PII detection and cannot guarantee detection of every identifier. The source
recording is not redacted or copied by this runner.

Every received JSON message is written as its original payload plus a newline
to `out.json.frames.jsonl`, including Begin, final Turn messages, speaker
revisions, errors, and Termination. No transcript or provider error content is
printed to the terminal. Speaker revisions are applied in arrival order, and
the final transcript is written atomically after successful termination as:

```json
{"turns": [{"speaker": "A", "text": "Hello [PERSON_NAME]."}]}
```

Turns are sorted by `turn_order`, with one final entry per turn; repeated turn
messages replace prior versions. Speaker letters identify voices, not staff
or patient roles. `PENDING` and `UNKNOWN` indicate unresolved attribution.

Success exits 0. Invalid inputs, API failures, configuration mismatches,
premature disconnections, and timeouts exit non-zero. A partial audit remains
available after failure; the final output is replaced only on success. Reusing
an output path overwrites its audit, so use a new path per call. Medical Mode,
diarization, and PII redaction are paid AssemblyAI add-ons.

Run local checks with `.venv/bin/python -m unittest -v test_transcribe.py`.
