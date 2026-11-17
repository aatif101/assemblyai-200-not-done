import json, os, sys, time, urllib.request, urllib.error

BASE = "https://api.assemblyai.com/v2"
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
        sys.exit(f"AssemblyAI {method} {path} failed: {e.code} {e.read().decode()[:300]}")


def main(audio, out):
    with open(audio, "rb") as f:
        upload_url = call("POST", "/upload", f.read(), raw=True)["upload_url"]
    policies = ["us_social_security_number", "phone_number", "person_name"]
    t = call("POST", "/transcript", {
        "audio_url": upload_url,
        "speaker_labels": True,
        "speakers_expected": 2,
        "redact_pii": True,
        "redact_pii_policies": policies,
        "redact_pii_sub": "entity_name",
    })
    tid = t["id"]
    while t["status"] not in ("completed", "error"):
        time.sleep(3)
        t = call("GET", f"/transcript/{tid}")
    if t["status"] != "completed":
        sys.exit(f"transcription failed: {t.get('error')}")
    # Refuse to write output unless redaction was actually applied.
    if not t.get("redact_pii") or set(policies) - set(t.get("redact_pii_policies") or []):
        sys.exit("PII redaction not confirmed on transcript; refusing to write output")
    utts = [{"speaker": u["speaker"], "text": u["text"]} for u in t.get("utterances") or []]
    with open(out, "w") as f:
        json.dump({"transcript_id": tid, "text": t["text"], "utterances": utts}, f, indent=2)


if len(sys.argv) != 3:
    sys.exit("usage: run.sh <audio.wav> <out.json>")
main(sys.argv[1], sys.argv[2])
