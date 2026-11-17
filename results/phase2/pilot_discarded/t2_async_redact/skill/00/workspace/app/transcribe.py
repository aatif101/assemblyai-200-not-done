import json, os, sys, time, urllib.request, urllib.error

BASE = "https://api.assemblyai.com"
KEY = os.environ.get("ASSEMBLYAI_API_KEY")


def call(method, path, body=None, raw=False):
    data = body if raw else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Authorization", KEY)
    if not raw and body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit(f"AssemblyAI {method} {path} failed: {e.code} {e.read().decode()[:500]}")


def main(audio, out):
    if not KEY:
        sys.exit("ASSEMBLYAI_API_KEY is not set")
    with open(audio, "rb") as f:
        up = call("POST", "/v2/upload", f.read(), raw=True)
    t = call("POST", "/v2/transcript", {
        "audio_url": up["upload_url"],
        "speech_models": ["universal-3-5-pro", "universal-2"],
        "language_code": "en",
        "speaker_labels": True,
        "speaker_options": {"min_speakers_expected": 2, "max_speakers_expected": 2},
        # Redaction happens server-side, so the stored copy is redacted too.
        "redact_pii": True,
        "redact_pii_policies": ["person_name", "phone_number",
                                "us_social_security_number", "number_sequence"],
        "redact_pii_sub": "entity_name",
        "redact_pii_audio": True,
        "redact_pii_audio_options": {"override_audio_redaction_method": "silence"},
    })
    tid = t["id"]
    deadline = time.time() + 3600
    while t["status"] not in ("completed", "error"):
        if time.time() > deadline:
            sys.exit(f"timed out waiting for transcript {tid}")
        time.sleep(3)
        t = call("GET", f"/v2/transcript/{tid}")
    if t["status"] == "error":
        sys.exit(f"transcription failed: {t.get('error')}")
    utts = [{"speaker": u["speaker"], "text": u["text"]} for u in t.get("utterances") or []]
    if not utts:
        sys.exit("no utterances returned")
    with open(out, "w") as f:
        json.dump({"transcript_id": tid, "text": t["text"], "utterances": utts}, f, indent=2)


if len(sys.argv) != 3:
    sys.exit("usage: run.sh <audio.wav> <out.json>")
main(sys.argv[1], sys.argv[2])
