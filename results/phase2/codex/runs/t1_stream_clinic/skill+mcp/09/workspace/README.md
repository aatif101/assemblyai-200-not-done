Run from a fresh shell (dependencies are installed in `.venv`):

```sh
export ASSEMBLYAI_API_KEY='your-key'
./run.sh audio.wav out.json
```

Accepts uncompressed 16-bit mono PCM WAV at 8–96 kHz, including the supplied
16 kHz sample. Sends raw PCM in paced 50 ms chunks, padding only the last short
chunk with silence. Returns exit status 0 only after AssemblyAI's `Termination`
message, all final turns, and speaker corrections have been processed. Failures
return nonzero and leave the received audit messages available for diagnosis.

`out.json` contains `{"turns": [{"speaker": "A", "text": "..."}]}` in turn order.
Each entry uses the final turn transcript and latest corrected speaker label.
Unresolved speakers remain `PENDING`. Labels identify voices, not clinical roles;
a turn-level label identifies the dominant speaker when a turn mixes voices.

`out.json.frames.jsonl` contains every received AssemblyAI application message,
in arrival order, with its exact JSON payload followed by a newline. No fields
are filtered or reserialized. Existing audit files are overwritten on each run;
the final transcript is replaced atomically only on success. Use a new output
path per call. The program does not print transcript text or copy input audio.

Uses Universal-3.6 Pro, `domain=medical-v1`, speaker diarization, and server-side
PII redaction. Partial turns are explicitly disabled because they are unredacted.
All supported PII categories are masked with entity placeholders; this includes
medical categories such as drugs, conditions, and procedures. The original
recording is unchanged. Medical Mode supports English, Spanish, German, and
French. Add-ons incur AssemblyAI usage charges.

The program checks the server's confirmed model, Medical Mode, redaction, and
speaker settings before sending audio. Server redaction is a model prediction,
so it cannot guarantee that every personal detail is detected. The raw audit
requirement means server messages, including any detection misses, are stored
unchanged. Validate redaction quality and your AssemblyAI account's retention
settings before using real patient calls.

Reference: [streaming PII redaction](https://www.assemblyai.com/docs/streaming/pii-redaction)
and [speaker labels and revisions](https://www.assemblyai.com/docs/streaming/label-speakers-and-separate-channels).

To recreate dependencies: `python3 -m venv .venv` then
`.venv/bin/python -m pip install -r requirements.txt`.
Run local checks with `.venv/bin/python -m unittest -v`.
