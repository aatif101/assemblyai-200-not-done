import json, os, sys, time, urllib.request

BASE = "https://api.assemblyai.com"
KEY = os.environ["ASSEMBLYAI_API_KEY"]


def call(method, path, data=None, headers=None, body=None):
    h = {"Authorization": KEY, **(headers or {})}
    if body is None and data is not None:
        body = json.dumps(data).encode()
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE + path, data=body, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit(f"HTTP {e.code} on {path}: {e.read().decode()}")


def main(audio, out):
    with open(audio, "rb") as f:
        upload_url = call("POST", "/v2/upload", body=f.read(),
                          headers={"Content-Type": "application/octet-stream"})["upload_url"]
    t = call("POST", "/v2/transcript", {
        "audio_url": upload_url,
        "speech_models": ["universal-3-5-pro", "universal-2"],
        "language_detection": True,
        "language_detection_options": {
            "expected_languages": ["en"],
            "fallback_language": "en",
            "localization": ["en_uk"],
        },
    })
    tid = t["id"]
    while t["status"] not in ("completed", "error"):
        time.sleep(2)
        t = call("GET", f"/v2/transcript/{tid}")
    if t["status"] == "error":
        sys.exit(f"Transcription failed: {t.get('error')}")
    print("language_code:", t.get("language_code"), file=sys.stderr)
    with open(out, "w") as f:
        json.dump({"transcript_id": tid, "text": t["text"] or ""}, f)


if len(sys.argv) != 3:
    sys.exit("usage: run.sh <audio.wav> <out.json>")
main(*sys.argv[1:])
