Run from this directory in a fresh shell (with ASSEMBLYAI_API_KEY exported):

```sh
./run.sh <audio.wav> <out.json>
```

Dependencies are already installed here. To install elsewhere, use Python 3.10+
with `python3 -m pip install -r requirements.txt`.

Input must be 16 kHz mono 16-bit PCM WAV. The runner reads PCM from the WAV,
sends 100 ms chunks at real-time speed, adds one second of trailing silence,
and waits for AssemblyAI's Termination message. It exits 0 only after writing
the complete result; errors exit non-zero. Existing outputs are refused to
protect earlier audit records. On failure the received audit frames remain,
but no result JSON is published.

Output is `{"turns":[{"speaker":"A","text":"..."}]}`. Turns are sorted by
AssemblyAI's turn order; formatted finals replace earlier versions. Speaker
revisions are applied, and absent speaker labels become UNKNOWN. Labels denote
voices, not names or clinic/patient roles.

`<out.json>.frames.jsonl` contains every received application JSON message in
arrival order, using its original text without reserialization. This includes
Begin, Turn, Termination, warnings, and errors. No transcript text or API key is
printed to the terminal. New files have owner-only permissions.

The connection uses AssemblyAI v3 streaming with:

- `speech_model=universal-streaming-english` and `domain=medical-v1`.
- `speaker_labels=true` and formatted turns.
- Server-side PII redaction with entity-name masks. Policies cover names,
  telephone numbers, SSNs, birth dates, ages, emails, addresses/locations,
  financial and healthcare identifiers, credentials and other identifiers.
  See PII_POLICIES in transcribe.py for the complete list. Medications,
  conditions and procedures remain available as clinical transcript content.
- Partial turns disabled so only completed, redacted turns are requested.
- Support audio capture disabled.

Before sending any audio, the runner requires the Begin response to confirm
medical mode, speaker labels, and PII redaction. It never falls back to an
unredacted connection. Server-side redaction is essential because the audit
requirement prohibits changing received messages.

PII detection is model-based: this cannot guarantee that every personal detail
will be recognized. An absolute guarantee of zero stored PII cannot be combined
with storing every server message unmodified if the service misses an entity.
The sample test verified masks in transcript, utterance, and word fields; it
is not a guarantee for arbitrary calls. The original input audio is neither
copied nor deleted by this program.

Verification:

```sh
python3 -m unittest -v test_transcribe.py
```

The supplied sample completed successfully in a real AssemblyAI session,
producing sample-output.json and sample-output.json.frames.jsonl (15 nonempty
final turns, 18 received messages including an empty terminal turn).
