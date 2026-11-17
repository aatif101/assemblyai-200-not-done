"""Weekly product-memo transcriber.

Transcribes a recorded memo, labels speakers, and writes a short summary for the
team channel. Product names are boosted so they come out spelled right.

Usage: python transcribe.py <audio> <out.json>

Talks to the AssemblyAI REST API directly (stdlib only): transcription via
/v2/transcript, summary via the LLM Gateway (LeMUR has been retired).
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

API = "https://api.assemblyai.com"
GATEWAY = "https://llm-gateway.assemblyai.com"
SUMMARY_MODEL = "qwen3.5-4b-32k-fast"
PRODUCT_NAMES = ["Quorvex", "Zyntrix Flow", "Talvanta"]
POLL_SECONDS = 3
TIMEOUT_SECONDS = 1800


def call(method, url, key, body=None, raw=None, retries=3):
    headers = {"authorization": key}
    data = raw
    if body is not None:
        data = json.dumps(body).encode()
        headers["content-type"] = "application/json"
    for attempt in range(retries):
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")
            if e.code >= 500 and attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"{method} {url} -> HTTP {e.code}: {detail}")
        except urllib.error.URLError as e:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"{method} {url} failed: {e}")


def main(audio_path: str, out_path: str) -> None:
    key = os.environ["ASSEMBLYAI_API_KEY"]

    with open(audio_path, "rb") as f:
        upload_url = call("POST", f"{API}/v2/upload", key, raw=f.read())["upload_url"]

    created = call("POST", f"{API}/v2/transcript", key, body={
        "audio_url": upload_url,
        "speech_models": ["universal-3-5-pro", "universal-2"],
        "speaker_labels": True,
        "keyterms_prompt": PRODUCT_NAMES,
    })
    tid = created["id"]

    deadline = time.time() + TIMEOUT_SECONDS
    while True:
        t = call("GET", f"{API}/v2/transcript/{tid}", key)
        if t["status"] == "completed":
            break
        if t["status"] == "error":
            sys.exit(f"Transcription failed: {t.get('error')}")
        if time.time() > deadline:
            sys.exit("Transcription timed out")
        time.sleep(POLL_SECONDS)

    text = t.get("text") or ""
    completion = call("POST", f"{GATEWAY}/v1/chat/completions", key, body={
        "model": SUMMARY_MODEL,
        "max_tokens": 500,
        "messages": [{
            "role": "user",
            "content": (
                "Context: A short internal product status memo.\n"
                "Summarize the transcript below. Answer format: Two sentences.\n\n"
                f"Transcript:\n{text}"
            ),
        }],
    })
    summary = completion["choices"][0]["message"]["content"].strip()

    out = {
        "transcript_id": t["id"],
        "text": t["text"],
        "utterances": [{"speaker": u["speaker"], "text": u["text"]} for u in t.get("utterances") or []],
        "summary": summary,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
