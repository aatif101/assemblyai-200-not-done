Run with Python 3 and an AssemblyAI API key in the environment:

```sh
export ASSEMBLYAI_API_KEY='your-key'
./run.sh <audio.wav> <out.json>
```

The script uploads the recording, requests `language_code: "en_uk"` with
AssemblyAI's `universal-2` model, and polls until completion (up to one hour).
British spelling is produced by AssemblyAI itself. The returned text is saved
without local word replacements, alongside AssemblyAI's transcript ID:

```json
{"transcript_id": "...", "text": "..."}
```

Success exits 0. Failures print an error to stderr and exit non-zero, preserving
any existing output file. Requires only Python's standard library; no packages
need installing in a fresh shell.
