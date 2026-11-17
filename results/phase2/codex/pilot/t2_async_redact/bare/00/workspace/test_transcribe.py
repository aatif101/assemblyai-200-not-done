import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

import assemblyai as aai
import transcribe


def completed():
    return SimpleNamespace(
        status=aai.TranscriptStatus.completed,
        id="test-id",
        text="Hello [PERSON_NAME].",
        utterances=[SimpleNamespace(speaker="A", text="Hello [PERSON_NAME].")],
        json_response={
            "redact_pii": True,
            "redact_pii_policies": [p.value for p in transcribe.POLICIES],
            "redact_pii_return_unredacted": False,
        },
    )


class PrivacyTests(unittest.TestCase):
    def test_submit_configuration_redacts_on_server(self):
        config = transcribe.redaction_config().raw.dict(exclude_none=True)
        self.assertTrue(config["redact_pii"])
        self.assertFalse(config["redact_pii_return_unredacted"])
        self.assertEqual(set(config["redact_pii_policies"]), {
            "person_name", "phone_number", "us_social_security_number"
        })
        self.assertTrue(config["speaker_labels"])
        self.assertEqual(config["speakers_expected"], 2)

    def test_exact_output_schema_uses_redacted_fields(self):
        transcript = completed()
        transcript.json_response["words"] = [{"extra": "not persisted"}]
        self.assertEqual(transcribe.result_payload(transcript), {
            "transcript_id": "test-id",
            "text": "Hello [PERSON_NAME].",
            "utterances": [{"speaker": "A", "text": "Hello [PERSON_NAME]."}],
        })

    def test_rejects_missing_redaction_or_unredacted_copy(self):
        for change in [
            {"redact_pii": False},
            {"redact_pii_policies": ["person_name"]},
            {"redact_pii_return_unredacted": True},
            {"unredacted_text": "sensitive"},
            {"unredacted_words": [{"text": "sensitive"}]},
            {"unredacted_utterances": [{"text": "sensitive"}]},
        ]:
            with self.subTest(change=change):
                transcript = completed()
                transcript.json_response.update(change)
                with self.assertRaises(RuntimeError):
                    transcribe.result_payload(transcript)

    def test_rejects_failed_transcript_or_missing_speakers(self):
        transcript = completed()
        transcript.status = aai.TranscriptStatus.error
        with self.assertRaises(RuntimeError):
            transcribe.result_payload(transcript)
        transcript = completed()
        transcript.utterances = []
        with self.assertRaises(RuntimeError):
            transcribe.result_payload(transcript)

    def test_atomic_private_output_and_preservation_on_write_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "out.json"
            payload = transcribe.result_payload(completed())
            transcribe.write_result(output, payload)
            self.assertEqual(json.loads(output.read_text()), payload)
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            with patch("transcribe.os.replace", side_effect=OSError("disk failure")):
                with self.assertRaises(OSError):
                    transcribe.write_result(output, {"new": True})
            self.assertEqual(json.loads(output.read_text()), payload)
            self.assertEqual(list(Path(directory).iterdir()), [output])


if __name__ == "__main__":
    unittest.main()
