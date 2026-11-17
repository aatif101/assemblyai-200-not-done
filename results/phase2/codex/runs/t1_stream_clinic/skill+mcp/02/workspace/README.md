Run from this directory with an exported API key:

```sh
export ASSEMBLYAI_API_KEY='your-key'
./run.sh audio.wav out.json
```

The installed `.venv` is used automatically in fresh shells. To recreate it:

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Input must be 16 kHz mono, signed 16-bit PCM WAV. Audio is read incrementally and sent as raw PCM in 50 ms chunks at real-time speed. The program uses AssemblyAI streaming v3, Universal-3.6 Pro, Medical Mode (`medical-v1`), speaker diarization, and server-side PII redaction with all supported policies. Partial turns are explicitly disabled because they are not redacted. Medical Mode supports English, Spanish, German, and French.

`out.json` contains final turns in turn order, with any subsequent speaker revisions applied. Speaker labels are anonymous identifiers such as A and B, not clinical roles; UNKNOWN or PENDING can remain if attribution is unavailable. The transcript text comes directly from the server's redacted `transcript`, never reconstructed from word metadata.

`out.json.frames.jsonl` records every received application JSON message, including Begin, Turn, SpeakerRevision, errors, and Termination. Payloads are written without parsing/reserializing them, followed by a newline, and flushed on receipt. No received transcript text is printed to the terminal. The service applies redaction before delivery; this client does not run a separate PII detector or alter the audit messages. Automatic detection can miss sensitive information, so this is not a guarantee that all PII is removed. The original audio is neither copied nor redacted by this program.

Before sending any audio, the client verifies the Begin configuration confirms the model, Medical Mode, diarization, and PII masking. It waits for Termination, including the final speaker revision, before atomically publishing out.json. Exit status is 0 only on success; errors, disconnection, timeouts, and invalid input produce nonzero status. On failure an existing out.json is left unchanged; the audit file for an attempted connection may be partial. New output files are private to the current user. Reusing an audit filename overwrites it.

The default AssemblyAI endpoint may route to US or EU processing. Endpoint and feature settings are defined in `transcribe.py`.

Protocol checks (local server; no API calls):

```sh
.venv/bin/python -m unittest -v test_transcribe.py
```

References: [PII masking](https://assemblyai.com/docs/streaming/pii-redaction), [medical settings](https://assemblyai.com/docs/streaming/medical-mode), [speaker revisions](https://assemblyai.com/docs/streaming/label-speakers-and-separate-channels).
