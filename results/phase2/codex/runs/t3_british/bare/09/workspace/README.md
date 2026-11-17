Run from this directory with `ASSEMBLYAI_API_KEY` set:

```sh
./run.sh <audio.wav> <out.json>
```

The AssemblyAI Python SDK is already installed in this environment. To install
it elsewhere, use `python3 -m pip install -r requirements.txt`.

The script uploads the recording, requests `language_code="en_uk"` with
`speech_models=["universal-3-5-pro", "universal-2"]`, and waits for completion
(up to two hours). The British locale requests regional English spelling from
AssemblyAI itself. No local word replacements are applied, so the text belongs
to the returned AssemblyAI transcript ID.

On success, it atomically writes exactly `{"transcript_id": "...", "text": "..."}`
and exits 0. Errors go to stderr and return a non-zero exit code without
overwriting the output. The output directory must already exist.
