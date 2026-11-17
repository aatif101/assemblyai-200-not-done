import json, os, sys, time, urllib.request

BASE = "https://api.assemblyai.com"
KEY = os.environ["ASSEMBLYAI_API_KEY"]


def call(method, path, data=None, headers=None):
    h = {"Authorization": KEY, **(headers or {})}
    req = urllib.request.Request(BASE + path, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit(f"{method} {path} -> {e.code}: {e.read().decode(errors='replace')}")


def main(audio, out):
    with open(audio, "rb") as f:
        up = call("POST", "/v2/upload", f.read(), {"Content-Type": "application/octet-stream"})
    body = {
        "audio_url": up["upload_url"],
        "speech_models": ["universal-3-5-pro", "universal-2"],
        "language_detection": True,
        "language_detection_options": {"localization": ["en_uk"]},
    }
    t = call("POST", "/v2/transcript", json.dumps(body).encode(), {"Content-Type": "application/json"})
    tid = t["id"]
    deadline = time.time() + 600
    while t["status"] not in ("completed", "error"):
        if time.time() > deadline:
            sys.exit("timed out")
        time.sleep(2)
        t = call("GET", f"/v2/transcript/{tid}")
    if t["status"] == "error":
        sys.exit("transcription failed: " + str(t.get("error")))
    print("language_code:", t.get("language_code"), file=sys.stderr)
    with open(out, "w") as f:
        json.dump({"transcript_id": tid, "text": t["text"]}, f)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: run.sh <audio.wav> <out.json>")
    main(*sys.argv[1:])
