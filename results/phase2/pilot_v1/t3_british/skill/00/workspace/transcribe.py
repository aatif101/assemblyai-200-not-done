import json, os, sys, time, urllib.request, urllib.error

BASE = "https://api.assemblyai.com"
KEY = os.environ.get("ASSEMBLYAI_API_KEY")


def call(method, path, data=None, headers=None, raw=False):
    h = {"Authorization": KEY}
    h.update(headers or {})
    if data is not None and not raw:
        data = json.dumps(data).encode()
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE + path, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit(f"HTTP {e.code} on {path}: {e.read().decode(errors='replace')}")


def main():
    if len(sys.argv) != 3:
        sys.exit("usage: transcribe.py <audio> <out.json>")
    if not KEY:
        sys.exit("ASSEMBLYAI_API_KEY not set")
    audio, out = sys.argv[1:]
    with open(audio, "rb") as f:
        up = call("POST", "/v2/upload", f.read(),
                  {"Content-Type": "application/octet-stream"}, raw=True)
    t = call("POST", "/v2/transcript", {
        "audio_url": up["upload_url"],
        "speech_models": ["universal-3-5-pro", "universal-2"],
        "language_detection": True,
        "language_detection_options": {
            "expected_languages": ["en"],
            "fallback_language": "en",
            "localization": ["en_uk"],
        },
    })
    tid = t["id"]
    while True:
        t = call("GET", f"/v2/transcript/{tid}")
        if t["status"] == "completed":
            break
        if t["status"] == "error":
            sys.exit(f"transcription failed: {t.get('error')}")
        time.sleep(2)
    with open(out, "w") as f:
        json.dump({"transcript_id": tid, "text": t["text"]}, f)
    print("language_code:", t.get("language_code"), file=sys.stderr)


main()
