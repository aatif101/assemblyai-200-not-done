import json, os, sys, time, urllib.request, urllib.error

BASE = "https://api.assemblyai.com/v2"
KEY = os.environ["ASSEMBLYAI_API_KEY"]


def call(method, path, data=None, headers=None, body=None):
    h = {"authorization": KEY}
    h.update(headers or {})
    if body is None and data is not None:
        body = json.dumps(data).encode()
        h["content-type"] = "application/json"
    req = urllib.request.Request(BASE + path, data=body, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit(f"HTTP {e.code}: {e.read().decode(errors='replace')}")


def main(audio, out):
    with open(audio, "rb") as f:
        up = call("POST", "/upload", body=f.read(),
                  headers={"content-type": "application/octet-stream"})
    t = call("POST", "/transcript", {"audio_url": up["upload_url"], "language_code": "en_uk"})
    tid = t["id"]
    while t["status"] not in ("completed", "error"):
        time.sleep(3)
        t = call("GET", f"/transcript/{tid}")
    if t["status"] == "error":
        sys.exit(f"Transcription failed: {t.get('error')}")
    with open(out, "w") as f:
        json.dump({"transcript_id": tid, "text": t["text"] or ""}, f)


if len(sys.argv) != 3:
    sys.exit("usage: run.sh <audio.wav> <out.json>")
main(sys.argv[1], sys.argv[2])
