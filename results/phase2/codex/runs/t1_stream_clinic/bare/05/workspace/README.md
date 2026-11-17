Run from this directory with Python 3.12 and `ASSEMBLYAI_API_KEY` set:

```sh
./run.sh sample.wav out.json
```

The dependency is already installed here. To recreate it elsewhere:

```sh
python3 -m pip install -r requirements.txt
```

The runner accepts uncompressed 16 kHz, mono, signed 16-bit PCM WAV files.
It sends PCM in 100 ms WebSocket packets to AssemblyAI v3 at real-time speed,
receives messages concurrently, and waits for `Termination` before publishing
`out.json`. A final short packet is padded with silence. The original WAV is
read in place and is never uploaded to the asynchronous file API or copied.

Configuration: `universal-streaming-english`, `domain=medical-v1`, speaker
labels, formatted turns, and server-side PII redaction with entity-name
placeholders. Partial turns and support audio capture are disabled. Identifying
PII policies are listed in `transcribe.py`. Conditions, medications and
procedures are retained. Broad number and date masking also covers identifier
fragments, but can mask medication doses, dates and other clinical numbers.

`out.json` contains the latest final text for each turn, ordered by
`turn_order`; formatted replacements and speaker-label revisions are applied.
Empty turns are omitted. Speaker labels identify voices (typically A/B), not
patient/staff roles. If the service supplies no usable label, it is `Unknown`.

Every incoming JSON message, including unknown message types, is immediately
written verbatim followed by a newline to `out.json.frames.jsonl`. It is not
parsed and reserialized for the audit file. Both files are created with private
permissions. Choose a new output path for each run: existing files are refused
to protect the audit trail. Exit status is 0 only after successful termination
and final output publication. On failure, the audit prefix is retained and no
final JSON is published. Server errors, warnings, unexpected disconnects, and
session timeouts fail the run; it does not silently retry or fall back to a
configuration without the requested features. Transcript text and API keys are
never printed to the console.

Privacy limitation: probabilistic server-side PII detection does not guarantee
that every personal detail is masked. Testing the supplied sample exposed a
numeric fragment left behind by the initial targeted policies; adding number-sequence
masking did not eliminate all numeric fragments in the follow-up test. Because the audit must preserve messages
unmodified, any missed PII will also be stored there. Do not treat this runner
as satisfying an absolute zero-PII-storage requirement. That requirement needs
a different audit policy or an upstream source that guarantees de-identification.
The source WAV itself also remains personal-data storage if it contains PII.
Provider account retention and healthcare data-processing arrangements must be
managed separately from this client.

Local protocol tests:

```sh
python3 -m unittest -v test_transcribe.py
```
