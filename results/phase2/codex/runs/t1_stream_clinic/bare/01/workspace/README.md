Run from this directory:

```sh
./run.sh sample.wav out.json
```

`ASSEMBLYAI_API_KEY` must be exported in your shell. Python 3.11+ and the pinned
`websockets` dependency are required; the dependency is already installed here.
To install elsewhere: `python3 -m pip install -r requirements.txt`.

The runner accepts uncompressed 16 kHz mono 16-bit PCM WAV. It streams PCM audio
in paced 100 ms chunks to AssemblyAI's v3 WebSocket endpoint, using
`universal-streaming-english` with the `medical-v1` domain, speaker diarization,
formatted turns, and server-side PII redaction. The last short chunk is padded
to the API's 50 ms minimum. It checks the server's confirmation of the medical,
speaker, and redaction settings before sending audio.

Personal and administrative identifiers are replaced with entity labels such as
`[PERSON_NAME]`. The explicit policy list is in `transcribe.py`. Clinical concepts
(drugs, conditions, procedures, injuries, and blood types) remain readable.
Partial turns are disabled; redacted final turns arrive throughout the call.
No transcript text or API credentials are printed to the console.

`out.json` contains `{"turns": [{"speaker": "A", "text": "..."}]}`. Turns are
ordered by the provider's turn index; later formatted versions and speaker
revisions replace earlier versions. Unassigned speakers are labeled `unknown`.
`out.json.frames.jsonl` contains every received application JSON message exactly
as received, in order, with a newline appended. This includes Begin, Turn,
SpeakerRevision, Termination, error, and unknown messages. The runner waits for
Termination before atomically writing the final output. Both files use private
file permissions. A failure returns nonzero, retains the received audit messages,
and leaves any previous final output untouched. Success returns zero.

PII detection and speaker identification are probabilistic. Masking happens at
AssemblyAI before messages reach this process, but this cannot guarantee that
every identifier is detected. The required unmodified audit file preserves any
provider redaction miss too; it cannot provide an absolute no-PII-storage
guarantee. Confirm the service's redaction performance and your account's data
handling terms before using real patient calls.

Verification:

```sh
python3 -m unittest -v test_transcribe.py
```

The live sample test completed successfully with 15 final turns, two speaker
labels, 13 redaction markers, and 19 received messages through Termination.
