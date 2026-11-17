Run from this directory with `ASSEMBLYAI_API_KEY` exported in your shell:

```bash
./run.sh <audio.wav> <out.json>
```

The installed `.venv` supplies the pinned WebSocket dependency; no activation is needed. To recreate it: `python3 -m venv .venv` followed by `.venv/bin/pip install -r requirements.txt`.

Input must be uncompressed 16 kHz mono 16-bit PCM WAV. The runner sends raw PCM in paced 50 ms chunks (the final chunk is silence-padded if needed). Audio is never uploaded to the batch transcription API or copied to a local file.

The v3 streaming session explicitly uses `universal-3-6-pro`, Medical Mode (`domain=medical-v1`), speaker diarization, and PII redaction with all default policies and entity-name placeholders. Partial turns are explicitly disabled: AssemblyAI only redacts final turns. Consequently live text arrives at turn boundaries. The server's Begin configuration must confirm the requested model, domain, diarization, and redaction before audio is sent. Diarization supplies its own turn-detection profile, so the `mode=max_accuracy` preset would have no effect here.

`<out.json>` contains `{"turns": [{"speaker": "A", "text": "..."}]}`. Turns are ordered by `turn_order`, with final speaker revisions applied. Labels identify voices rather than clinic/patient roles; unresolved labels may remain `PENDING` or `UNKNOWN`. The runner waits for the server's `Termination` before publishing the result atomically.

`<out.json>.frames.jsonl` contains every received JSON message as its original UTF-8 payload followed by a newline, including Begin, final turns, revisions, heartbeat messages, and Termination. It is flushed as messages arrive. No transcript text or credentials are printed. New output files are created with owner-only permissions. Errors return non-zero, retain the audit log, and do not publish a final transcript. Existing result/audit files at the requested paths are replaced when a session starts.

Masking relies on AssemblyAI's PII detection and cannot guarantee detection of every identifier. The audit intentionally preserves server messages without local rewriting. The source WAV itself remains unredacted; streaming redaction applies to transcript text, not audio. Validate detection on representative intake calls before handling patient data in production.

Run protocol checks with `.venv/bin/python -m unittest -v`.
