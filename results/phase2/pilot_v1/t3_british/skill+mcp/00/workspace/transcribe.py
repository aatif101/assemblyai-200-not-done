import json, os, sys, time, urllib.request

BASE = "https://api.assemblyai.com"
KEY = os.environ["ASSEMBLYAI_API_KEY"]


def call(method, path, data=None, raw=None):
    headers = {"authorization": KEY}
    body = raw
    if data is not None:
        body = json.dumps(data).encode()
        headers["content-type"] = "application/json"
    req = urllib.request.Request(BASE + path, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit(f"HTTP {e.code} from {path}: {e.read().decode()}")


def main(audio, out):
    with open(audio, "rb") as f:
        upload_url = call("POST", "/v2/upload", raw=f.read())["upload_url"]
    t = call("POST", "/v2/transcript", {
        "audio_url": upload_url,
        "language_code": "en_uk",  # British English spelling
        "speech_models": ["universal-3-pro", "universal-2"],
    })
    deadline = time.time() + 1800
    while t["status"] not in ("completed", "error"):
        if time.time() > deadline:
            sys.exit("timed out waiting for transcript")
        time.sleep(3)
        t = call("GET", f"/v2/transcript/{t['id']}")
    if t["status"] == "error":
        sys.exit(f"transcription failed: {t.get('error')}")
    with open(out, "w") as f:
        json.dump({"transcript_id": t["id"], "text": t["text"] or ""}, f)


if len(sys.argv) != 3:
    sys.exit("usage: run.sh <audio.wav> <out.json>")
main(*sys.argv[1:])
