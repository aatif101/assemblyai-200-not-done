Run with Python 3 and an exported `ASSEMBLYAI_API_KEY`:

```sh
./run.sh <audio.wav> <out.json>
```

No third-party dependencies are required. The script uploads the recording to
AssemblyAI, submits a recorded-audio transcription, and polls until completion
(up to two hours). It requests British spelling through
`language_detection_options.localization: ["en_uk"]`, with detection restricted
to English and English as the fallback. This applies spelling localization in
AssemblyAI itself.

On success, the output contains exactly `transcript_id` (AssemblyAI's transcript
ID) and `text` (AssemblyAI's returned transcript), and the command exits 0.
Errors go to stderr with a non-zero exit code. The output is replaced atomically
only after success; an existing output is preserved on failure.

Tested with `sample.wav`: AssemblyAI reported `en_uk` and returned “organise”,
“colour”, and “centre”. The result is saved in `sample-transcript.json`.
