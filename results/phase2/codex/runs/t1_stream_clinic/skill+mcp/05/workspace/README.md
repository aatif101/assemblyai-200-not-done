Run from this directory with the API key exported in your shell:

```sh
./run.sh sample.wav out.json
```

Requires uncompressed 16-bit PCM, 16 kHz mono WAV. The installed `.venv`
contains the pinned WebSocket dependency; `run.sh` locates it without requiring
shell activation. To recreate it, run `python3 -m venv .venv` and
`.venv/bin/python -m pip install -r requirements.txt`.

Audio is sent over AssemblyAI's v3 WebSocket as 50 ms PCM chunks at wall-clock
speed. A short final chunk is padded with silence. Medical Mode
(`domain=medical-v1`) and the `universal-3-6-pro` model improve medical vocabulary
recognition. Speaker diarization is enabled; final speaker revisions are applied
before publishing `out.json`. Labels such as A/B identify voices, not clinical
roles. Unresolved labels may remain PENDING/UNKNOWN.

PII redaction runs at AssemblyAI before delivery. Partial turns are explicitly
disabled because they are unredacted. You receive final turns as they complete,
rather than word-by-word drafts. All supported PII policies are enabled by
default, so clinical entities such as medications and conditions can also be
masked. Entity placeholders use `[PERSON_NAME]`, `[PHONE_NUMBER]`, etc.
Automated detection can miss entities; this implementation cannot guarantee that
every identifying detail is detected. Streaming does not redact the source audio
file. It does not create another audio copy.

`out.json.frames.jsonl` is flushed after every received application message.
It preserves the original JSON payload bytes and arrival order, appending a
newline after each message; it includes Begin, Turn, SpeakerRevision,
Termination, and any other messages received. No transcript text is printed to
the console. API errors are retained in the audit log. Output files are created
with private permissions. Existing output files are replaced on a valid run.

Exit status is zero only after successful termination and final JSON publication.
Failure returns nonzero and retains the audit messages received so far. The
runner does not reconnect/replay automatically, which could duplicate call audio
or produce conflicting speaker labels. Connection and shutdown waits are bounded.

Run the local protocol checks with:

```sh
.venv/bin/python -m unittest -v test_transcribe.py
```

References: [streaming PII redaction](https://www.assemblyai.com/docs/streaming/pii-redaction)
and the local [streaming skill reference](assemblyai/references/streaming.md).
