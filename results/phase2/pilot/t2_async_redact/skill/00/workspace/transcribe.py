import json, os, sys
import assemblyai as aai

def main(audio, out):
    key = os.environ.get("ASSEMBLYAI_API_KEY")
    if not key:
        sys.exit("ASSEMBLYAI_API_KEY not set")
    aai.settings.api_key = key
    config = aai.TranscriptionConfig(
        speech_models=["universal-3-5-pro", "universal-2"],
        speaker_labels=True,
        speakers_expected=2,
        redact_pii=True,
        redact_pii_policies=[
            aai.PIIRedactionPolicy.us_social_security_number,
            aai.PIIRedactionPolicy.phone_number,
            aai.PIIRedactionPolicy.person_name,
            aai.PIIRedactionPolicy.number_sequence,
        ],
        redact_pii_sub=aai.PIISubstitutionPolicy.entity_name,
    )
    t = aai.Transcriber().transcribe(audio, config)
    if t.status != aai.TranscriptStatus.completed:
        sys.exit(f"transcription failed: {t.error}")
    result = {
        "transcript_id": t.id,
        "text": t.text,
        "utterances": [{"speaker": u.speaker, "text": u.text} for u in (t.utterances or [])],
    }
    with open(out, "w") as f:
        json.dump(result, f, indent=2)

if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: run.sh <audio.wav> <out.json>")
    main(sys.argv[1], sys.argv[2])
