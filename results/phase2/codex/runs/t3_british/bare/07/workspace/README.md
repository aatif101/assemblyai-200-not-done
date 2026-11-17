Run with Python 3 and `ASSEMBLYAI_API_KEY` exported in your shell:

```sh
./run.sh <audio.wav> <out.json>
```

The required AssemblyAI SDK is already installed in this environment. To install
it elsewhere, run `python3 -m pip install -r requirements.txt`.

The script uploads the recording to AssemblyAI, requests transcription with
British English localisation (`language_detection_options.localization=["en_uk"]`),
and waits up to one hour for completion. It uses the Universal-3.5 Pro model with
Universal-2 as the fallback. Localisation applies British spelling throughout
AssemblyAI's returned transcript; no local word-replacement list is used.

Success writes exactly `{"transcript_id": "...", "text": "..."}` as UTF-8 JSON
and exits 0. Failures print an error to stderr and exit non-zero. An existing
output file is replaced atomically only after successful transcription.
