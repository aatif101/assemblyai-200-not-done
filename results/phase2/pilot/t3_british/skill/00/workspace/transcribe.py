import json, os, sys, time, urllib.request, urllib.error

BASE = "https://api.assemblyai.com"
KEY = os.environ["ASSEMBLYAI_API_KEY"]


def call(method, path, data=None, body=None, ctype=None):
    h = {"Authorization": KEY}
    if body is not None:
        data = json.dumps(body).encode()
        ctype = "application/json"
    if ctype:
        h["Content-Type"] = ctype
    req = urllib.request.Request(BASE + path, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise SystemExit(f"HTTP {e.code} on {path}: {e.read().decode()}")


def main(audio, out):
    with open(audio, "rb") as f:
        up = call("POST", "/v2/upload", data=f.read(), ctype="application/octet-stream")
    cfg = {
        "audio_url": up["upload_url"],
        "speech_models": ["universal-3-5-pro", "universal-2"],
        "language_detection": True,
        "language_detection_options": {
            "expected_languages": ["en"],
            "fallback_language": "en",
            "localization": ["en_uk"],
        },
    }
    t = call("POST", "/v2/transcript", body=cfg)
    tid = t["id"]
    deadline = time.time() + 900
    while t["status"] not in ("completed", "error"):
        if time.time() > deadline:
            raise SystemExit("timed out")
        time.sleep(2)
        t = call("GET", f"/v2/transcript/{tid}")
    if t["status"] == "error":
        raise SystemExit(f"transcription failed: {t.get('error')}")
    print("language_code:", t.get("language_code"), file=sys.stderr)
    with open(out, "w") as f:
        json.dump({"transcript_id": tid, "text": t["text"] or ""}, f)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: run.sh <audio.wav> <out.json>")
    main(*sys.argv[1:])
