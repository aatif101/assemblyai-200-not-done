Run from this directory with Python 3.12+ and `ASSEMBLYAI_API_KEY` exported:

```sh
./run.sh sample.wav out.json
```

The installed dependency is pinned in `requirements.txt`. To install in another
environment: `python3 -m pip install -r requirements.txt`.

Input must be uncompressed 16 kHz, mono, signed 16-bit PCM WAV. The program sends
only PCM samples, in 100 ms binary chunks paced with a monotonic clock. It receives
transcription concurrently and waits for AssemblyAI's `Termination` before
atomically writing `{"turns": [{"speaker": "A", "text": "..."}]}`. Silent
recordings produce an empty turns list. `UNKNOWN` represents a missing speaker
assignment; labels identify voices, not patient/staff roles.

The session uses `u3-rt-pro` with `mode=max_accuracy`, `domain=medical-v1`, and
speaker diarization. Final turn updates replace earlier versions; speaker
revisions are applied by turn order. The model alias currently resolves to
`universal-3-6-pro` (reported in the audit's Begin event).

PII redaction runs **on AssemblyAI's server**, before messages reach this client.
Partial turns are disabled so personal identifiers do not appear in provisional
transcripts. Explicit policies in `transcribe.py` mask names, phone numbers,
SSNs, birth dates, ages, addresses, email, account/healthcare/government IDs,
credentials, and other numeric identifiers with entity placeholders. Medication,
condition, and procedure names remain readable. The client checks the server's
confirmation of redaction, medical domain, and speaker labels before sending
audio. Audio capture for customer support is disabled.

Every received message is written verbatim, in arrival order, to
`<out.json>.frames.jsonl`, including Begin, final turn updates, speaker revisions,
errors, and Termination. Neither transcript nor audit is printed to the console;
both files have owner-only permissions. On failure the process exits nonzero,
retains any audit already received, and does not publish a new output JSON.
An existing output JSON is replaced only on success.

Automatic PII detection can miss identifiers. Because the required audit is
verbatim, this client cannot mask a server redaction miss without changing that
audit. The source audio also already contains personal details and is transmitted
to AssemblyAI; this program does not delete or copy that source recording. A
strict guarantee that no personal information is ever stored requires a provider
and retention arrangement that meets that requirement, beyond model redaction.

Run the local tests with `python3 -m unittest -v test_transcribe.py`.
