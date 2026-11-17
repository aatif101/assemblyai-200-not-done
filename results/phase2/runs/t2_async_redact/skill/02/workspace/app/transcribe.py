import json, os, sys, time, urllib.request, urllib.error

BASE = "https://api.assemblyai.com"
KEY = os.environ.get("ASSEMBLYAI_API_KEY")


def call(method, path, data=None, raw=False, retries=3):
    headers = {"Authorization": KEY}
    body = data
    if data is not None and not raw:
        body = json.dumps(data).encode()
        headers["Content-Type"] = "application/json"
    for i in range(retries):
        req = urllib.request.Request(BASE + path, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code >= 500 and i < retries - 1:
                time.sleep(2 ** i)
                continue
            raise SystemExit(f"HTTP {e.code} on {path}: {e.read().decode()[:300]}")
        except urllib.error.URLError:
            if i == retries - 1:
                raise
            time.sleep(2 ** i)


def main():
    if len(sys.argv) != 3:
        raise SystemExit("usage: transcribe.py <audio> <out.json>")
    if not KEY:
        raise SystemExit("ASSEMBLYAI_API_KEY not set")
    audio, out = sys.argv[1:]
    with open(audio, "rb") as f:
        up = call("POST", "/v2/upload", f.read(), raw=True)
    t = call("POST", "/v2/transcript", {
        "audio_url": up["upload_url"],
        "speech_models": ["universal-3-5-pro", "universal-2"],
        "speaker_labels": True,
        "speakers_expected": 2,
        "redact_pii": True,
        "redact_pii_policies": ["person_name", "phone_number", "us_social_security_number"],
        "redact_pii_sub": "entity_name",
        "redact_pii_return_unredacted": False,
    })
    tid = t["id"]
    deadline = time.time() + 1800
    while t["status"] not in ("completed", "error"):
        if time.time() > deadline:
            raise SystemExit(f"timeout waiting for transcript {tid}")
        time.sleep(3)
        t = call("GET", f"/v2/transcript/{tid}")
    if t["status"] == "error":
        raise SystemExit(f"transcription failed ({tid}): {t.get('error')}")
    result = {
        "transcript_id": tid,
        "text": t["text"] or "",
        "utterances": [{"speaker": u["speaker"], "text": u["text"]} for u in (t.get("utterances") or [])],
    }
    with open(out, "w") as f:
        json.dump(result, f, indent=2)


main()
