Run from this directory with `ASSEMBLYAI_API_KEY` exported:

```sh
./run.sh audio.wav out.json
```

The AssemblyAI SDK is already installed in this environment. To install it
elsewhere, run `python3 -m pip install -r requirements.txt`.

The script uploads the recording, requests `universal-2` with
`language_code="en_uk"`, waits for completion, and writes AssemblyAI's transcript
ID and text as UTF-8 JSON. The British locale controls spelling across the
transcript, including words such as “favour”, “analyse”, and “colour”.

Success exits 0. Invalid arguments, missing credentials/files, API errors, and
output errors exit non-zero, with an error on stderr. The output directory must
already exist. The destination is replaced only after a successful transcription.
