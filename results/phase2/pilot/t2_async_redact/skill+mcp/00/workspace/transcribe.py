import json, os, sys, time, urllib.request, urllib.error

BASE = "https://api.assemblyai.com"
KEY = os.environ.get("ASSEMBLYAI_API_KEY")
if not KEY:
    sys.exit("ASSEMBLYAI_API_KEY not set")


def call(method, path, data=None, ctype="application/json"):
    body = data if isinstance(data, bytes) else (json.dumps(data).encode() if data is not None else None)
    req = urllib.request.Request(BASE + path, data=body, method=method,
                                 headers={"Authorization": KEY, "Content-Type": ctype})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit(f"HTTP {e.code} on {path}: {e.read().decode()[:500]}")


audio, out = sys.argv[1], sys.argv[2]
with open(audio, "rb") as f:
    up = call("POST", "/v2/upload", f.read(), "application/octet-stream")

cfg = {
    "audio_url": up["upload_url"],
    "speech_models": ["universal-3-5-pro", "universal-2"],
    "speaker_labels": True,
    "speakers_expected": 2,
    # Redaction happens server-side, so the copy AssemblyAI keeps is redacted too.
    "redact_pii": True,
    "redact_pii_policies": ["us_social_security_number", "phone_number",
                            "person_name", "number_sequence"],
    "redact_pii_sub": "entity_name",
}
t = call("POST", "/v2/transcript", cfg)
tid = t["id"]
while t["status"] not in ("completed", "error"):
    time.sleep(3)
    t = call("GET", f"/v2/transcript/{tid}")
if t["status"] != "completed":
    sys.exit(f"transcription failed: {t.get('error')}")

result = {
    "transcript_id": tid,
    "text": t["text"],
    "utterances": [{"speaker": u["speaker"], "text": u["text"]} for u in t.get("utterances") or []],
}
with open(out, "w") as f:
    json.dump(result, f, indent=2)
