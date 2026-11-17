import json, os, sys, time, urllib.request, urllib.error

BASE = "https://api.assemblyai.com"


def call(method, path, data=None, raw=False):
    headers = {"Authorization": os.environ["ASSEMBLYAI_API_KEY"]}
    body = data
    if data is not None and not raw:
        body = json.dumps(data).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE + path, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise SystemExit(f"API error {e.code} on {path}: {e.read().decode(errors='replace')}")


def main():
    if len(sys.argv) != 3 or not os.environ.get("ASSEMBLYAI_API_KEY"):
        raise SystemExit("usage: run.sh <audio.wav> <out.json> (ASSEMBLYAI_API_KEY must be set)")
    audio, out = sys.argv[1:]
    with open(audio, "rb") as f:
        upload_url = call("POST", "/v2/upload", f.read(), raw=True)["upload_url"]

    # PII is redacted by AssemblyAI at processing time, so the stored copy is redacted too.
    t = call("POST", "/v2/transcript", {
        "audio_url": upload_url,
        "speech_models": ["universal-3-5-pro", "universal-2"],
        "speaker_labels": True,
        "speaker_options": {"min_speakers_expected": 2, "max_speakers_expected": 2},
        "redact_pii": True,
        "redact_pii_policies": ["us_social_security_number", "phone_number",
                                "person_name", "number_sequence"],
        "redact_pii_sub": "entity_name",
    })
    tid = t["id"]
    while t["status"] not in ("completed", "error"):
        time.sleep(3)
        t = call("GET", f"/v2/transcript/{tid}")
    if t["status"] == "error":
        raise SystemExit(f"transcription failed: {t.get('error')}")

    result = {
        "transcript_id": tid,
        "text": t["text"],
        "utterances": [{"speaker": u["speaker"], "text": u["text"]}
                       for u in t.get("utterances") or []],
    }
    with open(out, "w") as f:
        json.dump(result, f, indent=2)


main()
