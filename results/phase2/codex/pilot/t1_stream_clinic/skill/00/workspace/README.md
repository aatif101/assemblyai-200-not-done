Run from this directory with `ASSEMBLYAI_API_KEY` set:

```sh
./run.sh sample.wav out.json
```

Input must be 16 kHz mono 16-bit PCM WAV. The runner sends raw PCM in
50 ms chunks paced with a monotonic clock. It uses AssemblyAI Streaming v3,
Universal-3.6 Pro, Medical Mode (`medical-v1`), speaker diarization, and
PII redaction with `[ENTITY_TYPE]` replacements. The explicit policies in
`transcribe.py` cover names, phone numbers, SSNs, birth dates, addresses,
email, financial/account/healthcare identifiers, and personal demographics.
Medical terms (drugs, conditions, and procedures) remain readable; enabling
every PII policy would mask those too. Partials
are explicitly disabled because AssemblyAI redacts only final turns. Text
therefore arrives at turn completion rather than word by word.

The runner checks the server's model, Medical Mode, diarization, and PII
settings before sending audio. It collects finalized turns, replaces repeated
versions of a turn, applies all speaker revisions, and waits for `Termination`.
Speaker labels are anonymous (`A`, `B`, etc.); short or ambiguous turns may
remain `PENDING`/`UNKNOWN`. A turn boundary does not imply a speaker change.

`out.json` contains `{"turns": [{"speaker": "...", "text": "..."}]}` in
turn order. `out.json.frames.jsonl` contains every received AssemblyAI JSON
message, preserved verbatim and in receive order, including unknown message
types and speaker revisions. Both outputs use owner-only permissions. No call
text or API key is printed to the console, and no new audio copy is stored.
The original input WAV remains the caller's responsibility.

Exit status is 0 only after successful termination and output publication.
On failure, the audit log retains received frames and no new transcript is
published. Existing output files are replaced when running the same path.

PII masking is performed by AssemblyAI before messages arrive. Like other
automated recognition, it can miss entities; it cannot guarantee that every
personal detail is detected. The required verbatim audit log preserves exactly
what the service returns. Streaming does not redact the source audio.

Dependencies are installed in `.venv`, which `run.sh` uses directly even in a
fresh shell. To recreate the environment:

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Run protocol/pacing/failure tests with `.venv/bin/python -m unittest -v`.
