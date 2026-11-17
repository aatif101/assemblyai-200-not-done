import json, os, sys, time, urllib.request, urllib.error

BASE = "https://api.assemblyai.com/v2"


def call(method, path, key, body=None, raw=False):
    data = body if raw else (json.dumps(body).encode() if body is not None else None)
    headers = {"authorization": key}
    if not raw and body is not None:
        headers["content-type"] = "application/json"
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise SystemExit(f"AssemblyAI HTTP {e.code} on {path}: {e.read().decode()[:300]}")


def main():
    if len(sys.argv) != 3:
        raise SystemExit("usage: run.sh <audio.wav> <out.json>")
    audio, out = sys.argv[1:]
    key = os.environ.get("ASSEMBLYAI_API_KEY")
    if not key:
        raise SystemExit("ASSEMBLYAI_API_KEY not set")
    with open(audio, "rb") as f:
        up = call("POST", "/upload", key, f.read(), raw=True)
    cfg = {
        "audio_url": up["upload_url"],
        "speaker_labels": True,
        "speakers_expected": 2,
        # PII is redacted by AssemblyAI before the transcript is stored/returned
        "redact_pii": True,
        "redact_pii_policies": [
            "us_social_security_number",
            "phone_number",
            "person_name",
        ],
        "redact_pii_sub": "entity_name",
    }
    t = call("POST", "/transcript", key, cfg)
    tid = t["id"]
    while t["status"] not in ("completed", "error"):
        time.sleep(3)
        t = call("GET", f"/transcript/{tid}", key)
    if t["status"] == "error":
        raise SystemExit(f"transcription failed: {t.get('error')}")
    if not t.get("redact_pii"):
        raise SystemExit("PII redaction not applied; refusing to write output")
    result = {
        "transcript_id": tid,
        "text": t.get("text") or "",
        "utterances": [
            {"speaker": u["speaker"], "text": u["text"]}
            for u in (t.get("utterances") or [])
        ],
    }
    with open(out, "w") as f:
        json.dump(result, f, indent=2)


main()
