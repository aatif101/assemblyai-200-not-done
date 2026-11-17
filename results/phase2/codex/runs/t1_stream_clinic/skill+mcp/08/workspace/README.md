Run from this directory:

```sh
export ASSEMBLYAI_API_KEY='your-key'
./run.sh sample.wav out.json
```

Requires a 16 kHz mono 16-bit PCM WAV. The installed `.venv` is used directly,
so a fresh shell needs no activation. To reinstall dependencies:

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

The client streams raw PCM in paced 50 ms packets over AssemblyAI's v3
WebSocket. It enables Universal-3.6 Pro, `domain=medical-v1`, speaker labels,
and all supported PII policies with `[ENTITY_TYPE]` replacements. Medical
Mode supports English, Spanish, German and French. Conservative silence
thresholds allow pauses during clinical speech and identifier dictation.

PII redaction happens at AssemblyAI before final turns are delivered. Partial
turns are explicitly disabled because they are unredacted. Live results therefore
arrive at turn completion. All PII policies also cover sensitive medical
information; some clinical terms may consequently be masked in the output.
Automated PII detection can miss entities; this implementation cannot guarantee
that no personal details will ever appear. Streaming does not redact the source
audio. The program does not copy or store the input audio.

`out.json.frames.jsonl` contains every received JSON message in arrival order,
using its original payload plus a newline, including unknown event types.
`out.json` contains one entry per nonempty final turn, ordered by `turn_order`,
with the latest final text and all speaker revisions applied. Labels such as
`A` and `B` identify voices, not clinical roles; `PENDING`/`UNKNOWN` can remain
for voices the service cannot attribute. Consecutive turns are not merged.

The final JSON is replaced atomically only on success. Errors return nonzero,
retain the received audit messages, and leave any previous final JSON intact.
The client checks the server's Begin configuration before sending audio and
waits for Termination, including final speaker revisions. It sends no transcript
text to the terminal. Newly created files use owner-only permissions.

Run verification with `.venv/bin/python -m unittest -v test_transcribe.py`.

References: [Medical Mode](https://www.assemblyai.com/docs/streaming/medical-mode),
[Streaming PII](https://www.assemblyai.com/docs/streaming/pii-redaction),
[Speaker labels](https://www.assemblyai.com/docs/streaming/label-speakers-and-separate-channels).
