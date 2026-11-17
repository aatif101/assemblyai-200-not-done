import json, os, sys, time, urllib.request

BASE = "https://api.assemblyai.com"
KEY = os.environ["ASSEMBLYAI_API_KEY"]


def call(method, path, data=None, headers=None):
    h = {"Authorization": KEY}
    h.update(headers or {})
    req = urllib.request.Request(BASE + path, data=data, headers=h, method=method)
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.load(r)


def main(audio, out):
    with open(audio, "rb") as f:
        up = call("POST", "/v2/upload", f.read(), {"Content-Type": "application/octet-stream"})
    body = {
        "audio_url": up["upload_url"],
        "speech_models": ["universal-3-5-pro", "universal-2"],
        "speaker_labels": True,
        "speakers_expected": 2,
        # Redaction is applied by AssemblyAI at transcription time, so the
        # copy they store is redacted too.
        "redact_pii": True,
        "redact_pii_policies": ["person_name", "phone_number", "us_social_security_number", "number_sequence"],
        "redact_pii_sub": "entity_name",
    }
    t = call("POST", "/v2/transcript", json.dumps(body).encode(), {"Content-Type": "application/json"})
    tid = t["id"]
    while t["status"] not in ("completed", "error"):
        time.sleep(3)
        t = call("GET", "/v2/transcript/" + tid)
    if t["status"] != "completed":
        sys.exit("transcription failed: %s" % t.get("error"))
    result = {
        "transcript_id": tid,
        "text": t["text"] or "",
        "utterances": [{"speaker": u["speaker"], "text": u["text"]} for u in (t.get("utterances") or [])],
    }
    with open(out, "w") as f:
        json.dump(result, f, indent=2)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: run.sh <audio.wav> <out.json>")
    main(*sys.argv[1:])
