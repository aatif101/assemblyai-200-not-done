import json, os, sys, time, urllib.request, urllib.error

BASE = "https://api.assemblyai.com/v2"
KEY = os.environ.get("ASSEMBLYAI_API_KEY")
POLICIES = ["us_social_security_number", "phone_number", "person_name"]


def call(method, path, data=None, raw=False, retries=3):
    headers = {"authorization": KEY}
    body = data
    if data is not None and not raw:
        body = json.dumps(data).encode()
        headers["content-type"] = "application/json"
    elif raw:
        headers["content-type"] = "application/octet-stream"
    for i in range(retries):
        req = urllib.request.Request(BASE + path, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code >= 500 and i < retries - 1:
                time.sleep(2 * (i + 1)); continue
            raise RuntimeError(f"HTTP {e.code} {method} {path}: {e.read().decode()[:300]}")
        except urllib.error.URLError:
            if i < retries - 1:
                time.sleep(2 * (i + 1)); continue
            raise


def main(audio, out):
    if not KEY:
        sys.exit("ASSEMBLYAI_API_KEY not set")
    with open(audio, "rb") as f:
        upload_url = call("POST", "/upload", f.read(), raw=True)["upload_url"]
    tid = call("POST", "/transcript", {
        "audio_url": upload_url,
        "speaker_labels": True,
        "speakers_expected": 2,
        "redact_pii": True,
        "redact_pii_policies": POLICIES,
        "redact_pii_sub": "entity_name",
        "redact_pii_audio": False,
    })["id"]
    try:
        while True:
            t = call("GET", f"/transcript/{tid}")
            if t["status"] == "completed":
                break
            if t["status"] == "error":
                raise RuntimeError("transcription failed: " + str(t.get("error")))
            time.sleep(3)
        if not t.get("redact_pii") or set(t.get("redact_pii_policies") or []) != set(POLICIES):
            raise RuntimeError("PII redaction not confirmed; refusing to write output")
        result = {
            "transcript_id": tid,
            "text": t["text"] or "",
            "utterances": [{"speaker": u["speaker"], "text": u["text"]}
                           for u in (t.get("utterances") or [])],
        }
    finally:
        # remove AssemblyAI's stored copy (transcript data)
        try:
            call("DELETE", f"/transcript/{tid}")
        except Exception as e:
            print(f"warning: could not delete transcript {tid}: {e}", file=sys.stderr)
            globals()["_del_failed"] = True
    if globals().get("_del_failed"):
        raise RuntimeError("could not delete remote transcript")
    with open(out, "w") as f:
        json.dump(result, f, indent=2)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: run.sh <audio.wav> <out.json>")
    try:
        main(*sys.argv[1:])
    except Exception as e:
        print("error:", e, file=sys.stderr); sys.exit(1)
