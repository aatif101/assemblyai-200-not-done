This repo is our weekly memo transcriber. Someone wrote it a while ago and it has stopped working. Please get it working again on the current AssemblyAI API. It should still do everything it did before: speaker labels, the product names spelled correctly, and the summary.

Keep the command line and the output format the same (see README.md).

My AssemblyAI API key is in the `ASSEMBLYAI_API_KEY` environment variable. There is a sample recording at `./sample.wav` (16 kHz mono WAV) you can test with. Anything you install in this environment stays installed.

When you are done, I will run it myself from this directory, in a fresh shell, as:

    ./run.sh <audio.wav> <out.json>

It must exit 0 on success and non-zero on failure.
