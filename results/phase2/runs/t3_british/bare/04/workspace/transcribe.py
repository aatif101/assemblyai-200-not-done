import json, os, sys, time, urllib.request

BASE = "https://api.assemblyai.com/v2"
KEY = os.environ["ASSEMBLYAI_API_KEY"]


def call(method, path, data=None, headers=None):
    h = {"authorization": KEY, **(headers or {})}
    req = urllib.request.Request(BASE + path, data=data, headers=h, method=method)
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)


def main(audio, out):
    with open(audio, "rb") as f:
        url = call("POST", "/upload", f.read(), {"content-type": "application/octet-stream"})["upload_url"]
    body = json.dumps({"audio_url": url, "language_code": "en_uk"}).encode()
    t = call("POST", "/transcript", body, {"content-type": "application/json"})
    while t["status"] not in ("completed", "error"):
        time.sleep(3)
        t = call("GET", "/transcript/" + t["id"])
    if t["status"] == "error":
        sys.exit("transcription failed: %s" % t.get("error"))
    with open(out, "w") as f:
        json.dump({"transcript_id": t["id"], "text": t["text"]}, f)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: run.sh <audio.wav> <out.json>")
    main(sys.argv[1], sys.argv[2])
