Run from this directory with Python 3.12+ and an exported AssemblyAI key:

```sh
export ASSEMBLYAI_API_KEY=your-key
./run.sh sample.wav out.json
```

The input must be uncompressed 16-bit, 16 kHz mono PCM WAV. The runner sends
100 ms audio chunks at real-time speed through AssemblyAI's v3 WebSocket API.
It requests `u3-rt-pro`, `domain=medical-v1`, `mode=max_accuracy`, speaker
diarization, and server-side PII redaction. It verifies the required features
in the Begin message before sending audio. The service currently resolves
this model request to `universal-3-6-pro`.

`out.json` contains `{"turns":[{"speaker":"A","text":"..."}]}`.
Repeated versions of a turn replace earlier versions; formatted final turns
and subsequent speaker revisions are used. An unavailable speaker is labeled
`Unknown`. `out.json.frames.jsonl` contains every received JSON message in
arrival order, preserving each original payload exactly and adding only a
newline. It is flushed after every message. Partial turns are disabled.
The final output is published atomically only after confirmed termination.
Failures return nonzero and retain the audit messages received so far; an
existing final output is left in place on failure, so always check exit status.

Redaction covers names, phone numbers, SSNs, email, birth dates, ages,
addresses/locations, healthcare and financial identifiers, account numbers,
credentials, and other identifying number sequences. Medications, conditions,
and procedures remain readable for clinical use. No client-side transcript
redaction changes the audit log. Automatic PII detection can miss entities;
the raw audit requirement means any service-side miss would also appear in
that log. This implementation therefore cannot guarantee that no personal
details are ever stored. The original input WAV remains on disk.

The `websockets` 17.4 dependency and its license are included in `.vendor`;
no installation or network package download is needed in a fresh shell.
No API key is written to files or printed. Audio capture for provider support
is explicitly disabled. Provider retention/account policies are separate from
these local output settings.

To run the local protocol and failure tests:

```sh
PYTHONPATH=.vendor python3 -m unittest -v test_transcribe.py
```
