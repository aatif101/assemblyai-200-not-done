Run from this directory with `ASSEMBLYAI_API_KEY` exported:

```sh
./run.sh sample.wav out.json
```

Python 3 and `websockets==15.0.1` are required; the dependency is already installed
in this environment. Elsewhere, use `python3 -m pip install -r requirements.txt`.

Input must be uncompressed 16 kHz mono, 16-bit PCM WAV. Audio data is sent in
100 ms packets at real-time speed (the last packet is padded with silence).
One second of silence and a forced endpoint
flush trailing speech, followed by Terminate. Success requires AssemblyAI's
Termination acknowledgment. Network, authentication, provider, file, and format
errors produce a nonzero exit code. There is no automatic replay after failure.

The streaming request uses `u3-rt-pro`, `mode=max_accuracy`, `domain=medical-v1`,
speaker labels, and server-side PII redaction with entity-name placeholders.
The provider may resolve the model alias to a newer model. No audio is sent
until Begin confirms redaction, diarization, and the medical domain. Partial
turns are disabled. Names, contact details, birth dates, ages, government IDs,
healthcare identifiers, account/payment information, and other configured
identifiers are redacted. See `PII_POLICIES` in transcribe.py for the exact list.
Medication names, conditions, and procedures are deliberately preserved.

`out.json` contains final turns sorted by turn_order, with formatting updates
replacing previous versions and speaker revisions applied. Empty turns are
omitted. Speakers use AssemblyAI labels; absent labels are `Unknown`, without
guessing staff/patient roles. Final output is published atomically on success;
an existing output is left intact on failure.

`out.json.frames.jsonl` is written incrementally, in receive order, including
Begin, partials if any, final turns, warnings/errors, and Termination. Each raw
WebSocket application message is written verbatim followed by a newline; no
fields are filtered or reserialized. The log is retained on failure and replaced
on the next invocation with the same output path. Both files have mode 0600.
Transcript content and credentials are never printed to the terminal.

PII detection is probabilistic. Server-side masking reduces exposure but cannot
guarantee that every identifier is recognized. The required unmodified audit log
will retain any PII the provider misses, including in word-level fields. If zero
PII retention is a hard requirement, that guarantee needs a separate operational
decision before using this with patient calls. Confirm your AssemblyAI account's
healthcare agreement and retention settings; this runner disables support audio
capture but cannot configure account-level retention.

Run local protocol, pacing, and failure tests with:

```sh
python3 -m unittest -v test_transcribe.py
```
