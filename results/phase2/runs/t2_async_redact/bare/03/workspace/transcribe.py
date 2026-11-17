import json, os, sys, time, urllib.request, urllib.error

BASE = "https://api.assemblyai.com"
KEY = os.environ["ASSEMBLYAI_API_KEY"]


def call(method, path, data=None, raw=False):
    headers = {"authorization": KEY}
    body = data
    if data is not None and not raw:
        body = json.dumps(data).encode()
        headers["content-type"] = "application/json"
    req = urllib.request.Request(BASE + path, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise SystemExit(f"AssemblyAI {method} {path} failed: {e.code} {e.read().decode()[:300]}")


def main(audio, out):
    with open(audio, "rb") as f:
        up = call("POST", "/v2/upload", f.read(), raw=True)
    t = call("POST", "/v2/transcript", {
        "audio_url": up["upload_url"],
        "speaker_labels": True,
        "speakers_expected": 2,
        "redact_pii": True,
        "redact_pii_policies": ["us_social_security_number", "phone_number", "person_name"],
        "redact_pii_sub": "hash",
    })
    tid = t["id"]
    while t["status"] not in ("completed", "error"):
        time.sleep(3)
        t = call("GET", f"/v2/transcript/{tid}")
    if t["status"] == "error":
        raise SystemExit(f"Transcription {tid} failed: {t.get('error')}")
    result = {
        "transcript_id": tid,
        "text": t.get("text") or "",
        "utterances": [{"speaker": u["speaker"], "text": u["text"]} for u in (t.get("utterances") or [])],
    }
    with open(out, "w") as f:
        json.dump(result, f, indent=2)


if len(sys.argv) != 3:
    raise SystemExit("usage: run.sh <audio.wav> <out.json>")
main(sys.argv[1], sys.argv[2])
