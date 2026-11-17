import json, os, sys, time, urllib.request, urllib.error

BASE = "https://api.assemblyai.com/v2"
KEY = os.environ["ASSEMBLYAI_API_KEY"]


def call(method, path, data=None, body=None):
    headers = {"authorization": KEY}
    if body is not None:
        data = json.dumps(body).encode()
        headers["content-type"] = "application/json"
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit(f"AssemblyAI HTTP {e.code}: {e.read().decode(errors='replace')}")


def main(audio, out):
    with open(audio, "rb") as f:
        up = call("POST", "/upload", data=f.read())
    t = call("POST", "/transcript", body={
        "audio_url": up["upload_url"],
        "language_code": "en_uk",
    })
    while t["status"] not in ("completed", "error"):
        time.sleep(3)
        t = call("GET", "/transcript/" + t["id"])
    if t["status"] == "error":
        sys.exit("Transcription failed: " + str(t.get("error")))
    with open(out, "w") as f:
        json.dump({"transcript_id": t["id"], "text": t["text"]}, f)


if len(sys.argv) != 3:
    sys.exit("usage: run.sh <audio.wav> <out.json>")
main(sys.argv[1], sys.argv[2])
