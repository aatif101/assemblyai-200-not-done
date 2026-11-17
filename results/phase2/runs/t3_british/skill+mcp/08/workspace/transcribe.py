import json, os, sys, time, urllib.request

BASE = "https://api.assemblyai.com/v2"
KEY = os.environ["ASSEMBLYAI_API_KEY"]


def call(method, path, body=None, raw=None):
    headers = {"authorization": KEY}
    data = raw
    if body is not None:
        data = json.dumps(body).encode()
        headers["content-type"] = "application/json"
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit(f"HTTP {e.code}: {e.read().decode()}")


def main(audio, out):
    with open(audio, "rb") as f:
        upload_url = call("POST", "/upload", raw=f.read())["upload_url"]
    t = call("POST", "/transcript", {"audio_url": upload_url, "language_code": "en_uk"})
    while t["status"] not in ("completed", "error"):
        time.sleep(3)
        t = call("GET", "/transcript/" + t["id"])
    if t["status"] == "error":
        sys.exit("Transcription failed: " + str(t.get("error")))
    with open(out, "w") as f:
        json.dump({"transcript_id": t["id"], "text": t["text"]}, f)


if len(sys.argv) != 3:
    sys.exit("usage: run.sh <audio> <out.json>")
main(*sys.argv[1:])
