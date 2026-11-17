import json, os, sys, time, urllib.request, urllib.error

BASE = "https://api.assemblyai.com"
KEY = os.environ.get("ASSEMBLYAI_API_KEY")


def call(method, path, data=None, headers=None, retries=3):
    h = {"Authorization": KEY}
    h.update(headers or {})
    for i in range(retries):
        req = urllib.request.Request(BASE + path, data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code < 500 and e.code != 429 or i == retries - 1:
                raise SystemExit(f"HTTP {e.code} on {path}: {e.read().decode()[:500]}")
        except urllib.error.URLError:
            if i == retries - 1:
                raise
        time.sleep(2 ** i)


def main():
    if len(sys.argv) != 3:
        sys.exit("usage: run.sh <audio.wav> <out.json>")
    if not KEY:
        sys.exit("ASSEMBLYAI_API_KEY is not set")
    audio, out = sys.argv[1:]
    with open(audio, "rb") as f:
        up = call("POST", "/v2/upload", f.read(), {"Content-Type": "application/octet-stream"})
    body = {
        "audio_url": up["upload_url"],
        "speech_models": ["universal-3-5-pro", "universal-2"],
        "speaker_labels": True,
        "speakers_expected": 2,
        "redact_pii": True,
        "redact_pii_policies": [
            "us_social_security_number", "phone_number", "person_name",
            "email_address", "date_of_birth", "credit_card_number",
        ],
        "redact_pii_sub": "hash",
    }
    t = call("POST", "/v2/transcript", json.dumps(body).encode(), {"Content-Type": "application/json"})
    tid = t["id"]
    deadline = time.time() + 1800
    while True:
        t = call("GET", f"/v2/transcript/{tid}")
        if t["status"] == "completed":
            break
        if t["status"] == "error":
            sys.exit(f"transcription failed: {t.get('error')}")
        if time.time() > deadline:
            sys.exit("timed out")
        time.sleep(3)
    result = {
        "transcript_id": tid,
        "text": t["text"] or "",
        "utterances": [{"speaker": u["speaker"], "text": u["text"]} for u in t.get("utterances") or []],
    }
    with open(out, "w") as f:
        json.dump(result, f, indent=2)


main()
