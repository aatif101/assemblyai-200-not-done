Run from this directory with Python 3 and `ASSEMBLYAI_API_KEY` set:

```sh
./run.sh sample.wav out.json
```

No third-party dependencies are required. The script uploads the recording,
submits an asynchronous AssemblyAI transcript using Universal-3.5 Pro and
`language_code: "en_uk"`, and polls until completion. This selects British
English spelling for the full transcript, including words beyond a fixed
replacement list. The transcript text is returned directly by AssemblyAI.

The JSON output contains exactly `transcript_id` and `text`. Success exits 0;
failures print an error to stderr and exit non-zero. Output is replaced
atomically after success. HTTP requests have a 120-second timeout and polling
has a 30-minute limit. Failed runs preserve any existing output file.

AssemblyAI documents the direct `en_uk` setting in the notes under
[Choose a locale](https://www.assemblyai.com/docs/pre-recorded-audio/language-detection#choose-a-locale).
