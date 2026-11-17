Run from this directory with the key exported in your shell:

```sh
./run.sh sample.wav out.json
```

Input must be 16 kHz mono 16-bit PCM WAV. The installed `.venv` contains the
dependency; no installation or shell activation is needed for subsequent runs.
To recreate it: `python3 -m venv .venv` then
`.venv/bin/pip install -r requirements.txt`.

The runner sends raw PCM in 50 ms chunks at real-time speed to AssemblyAI's v3
Streaming API using `universal-3-6-pro`, Medical Mode (`medical-v1`), speaker
diarization, and PII redaction for all supported policies. Partial turns are
explicitly disabled: AssemblyAI redacts only final turns. Text therefore arrives
at turn boundaries rather than word by word. Medical Mode supports English,
Spanish, German, and French.

`out.json` contains one entry per finalized API turn, ordered by `turn_order`.
Later versions of a turn replace earlier ones. Speaker revisions update labels
before output is written. Speakers are anonymous labels such as `A` and `B`;
unresolved labels remain `PENDING`. Each turn uses its dominant speaker label;
the audit frames also contain any word-level speaker assignments.

`out.json.frames.jsonl` records every received JSON message payload unchanged,
in arrival order, followed by a newline. This includes Begin, Turn,
SpeakerRevision, Termination, and any other message types. It is flushed after
each message and retained on failure. The final output is replaced atomically
only after successful termination; a failed run leaves any previous output
untouched. Both output files are created with owner-only permissions.

The runner does not copy audio or print transcript text. Redaction depends on
AssemblyAI's detection and cannot guarantee every identifier will be caught.
The supplied input WAV itself remains unchanged and may contain personal data.

References: [streaming PII redaction](https://assemblyai.com/docs/streaming/pii-redaction),
[Medical Mode](https://assemblyai.com/docs/streaming/medical-mode),
[speaker labels and revisions](https://assemblyai.com/docs/streaming/label-speakers-and-separate-channels).

Run local protocol and pacing checks with `.venv/bin/python -m unittest -v`.
