Run from this directory:

```sh
./run.sh sample.wav out.json
```

`ASSEMBLYAI_API_KEY` must be exported in the shell. Python 3.10+ and
`websockets==17.4` are required; the dependency is already installed in this
environment. To install elsewhere: `python3 -m pip install -r requirements.txt`.

The input must be 16 kHz mono, signed 16-bit PCM WAV. The runner sends only PCM
samples in 100 ms chunks paced by a monotonic clock. It uses AssemblyAI's v3
streaming endpoint with `universal-3-6-pro`, `mode=max_accuracy`, the
`medical-v1` domain, and speaker diarization. Audio support capture is disabled.

PII redaction runs at AssemblyAI before messages are returned. Partial turns are
disabled so provisional text is not written to the audit log. Policies cover
names, contact details, addresses, dates, ages, government/healthcare/financial
identifiers, credentials, and number sequences. Clinical terms (drugs,
conditions, procedures) remain readable. Policies are listed in `transcribe.py`.
Automated recognition and redaction can miss information; these settings are
not a guarantee that every personal detail will be detected.

`out.json` contains `{"turns": [{"speaker": "A", "text": "..."}]}`.
Final updates replace earlier versions of the same server turn; speaker
revisions are applied, and mixed-speaker turns are split using word labels.
Speaker labels identify voices, not clinical roles. Unassigned voices are
labelled `UNKNOWN`.

`out.json.frames.jsonl` receives every inbound AssemblyAI JSON payload in receive
order, unchanged, with a newline appended. This includes session, speech, turn,
warning, error, and termination messages. Nothing is filtered or locally masked
in this file. The API key and audio are not written to either output, and call
text is not printed to the terminal. New files have owner-only permissions.

The runner waits for `Termination` before atomically publishing the final JSON.
It exits 0 on success and nonzero on invalid input, connection/provider errors,
timeouts, or incomplete sessions. On failure the audit remains available and
no new final transcript is published. Existing final output is replaced only on
success. Run `python3 -m unittest -v` for local protocol and pacing checks.
