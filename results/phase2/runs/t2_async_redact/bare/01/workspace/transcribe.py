import json, os, sys, time, urllib.request, urllib.error

BASE = "https://api.assemblyai.com"


def call(method, path, key, body=None, raw=None):
    headers = {"authorization": key}
    data = raw
    if body is not None:
        data = json.dumps(body).encode()
        headers["content-type"] = "application/json"
    elif raw is not None:
        headers["content-type"] = "application/octet-stream"
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise SystemExit(f"AssemblyAI HTTP {e.code}: {e.read().decode(errors='replace')}")


def main():
    if len(sys.argv) != 3:
        raise SystemExit("usage: run.sh <audio.wav> <out.json>")
    audio, out = sys.argv[1:]
    key = os.environ.get("ASSEMBLYAI_API_KEY")
    if not key:
        raise SystemExit("ASSEMBLYAI_API_KEY not set")
    with open(audio, "rb") as f:
        upload_url = call("POST", "/v2/upload", key, raw=f.read())["upload_url"]

    # Redaction is requested at creation so the copy AssemblyAI stores is redacted too.
    t = call("POST", "/v2/transcript", key, {
        "audio_url": upload_url,
        "speaker_labels": True,
        "speakers_expected": 2,
        "redact_pii": True,
        "redact_pii_policies": ["us_social_security_number", "phone_number",
                                "person_name", "number_sequence"],
        "redact_pii_sub": "entity_name",
    })
    tid = t["id"]
    while t["status"] not in ("completed", "error"):
        time.sleep(3)
        t = call("GET", f"/v2/transcript/{tid}", key)
    if t["status"] == "error":
        raise SystemExit(f"transcription failed ({tid}): {t.get('error')}")

    utts = [{"speaker": u["speaker"], "text": u["text"]} for u in (t.get("utterances") or [])]
    if not utts:
        raise SystemExit(f"no utterances returned ({tid})")
    with open(out, "w") as f:
        json.dump({"transcript_id": tid, "text": t["text"], "utterances": utts}, f, indent=2)


main()
