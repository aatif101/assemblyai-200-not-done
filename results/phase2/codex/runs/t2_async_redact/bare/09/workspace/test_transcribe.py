import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import transcribe


class TranscriptionTests(unittest.TestCase):
    def completed(self):
        return {
            "id": "test-123", "status": "completed", "redact_pii": True,
            "redact_pii_policies": transcribe.POLICIES,
            "text": "Hello [PERSON_NAME].",
            "utterances": [{"speaker": "A", "text": "Hello [PERSON_NAME].", "start": 0}],
        }

    def test_request_redacts_on_server_and_preserves_speaker_labels(self):
        done = self.completed()
        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / "call.wav"
            audio.write_bytes(b"audio")
            with patch.object(transcribe, "request", side_effect=[
                {"upload_url": "https://cdn.assemblyai.com/upload/test"},
                {"id": "test-123", "status": "queued"}, done,
            ]) as request, patch.object(transcribe.time, "sleep"):
                result = transcribe.transcribe(audio, "test-key")
        config = json.loads(request.call_args_list[1].args[3])
        self.assertTrue(config["redact_pii"])
        self.assertFalse(config["redact_pii_return_unredacted"])
        self.assertEqual(set(config["redact_pii_policies"]), set(transcribe.POLICIES))
        self.assertTrue(config["speaker_labels"])
        self.assertEqual(config["speakers_expected"], 2)
        self.assertEqual([call.args[1] for call in request.call_args_list], ["POST", "POST", "GET"])
        self.assertEqual(result, {"transcript_id": "test-123", "text": done["text"],
                                  "utterances": [{"speaker": "A", "text": done["text"]}]})

    def test_rejects_unredacted_response(self):
        for overrides in ({"redact_pii": False}, {"unredacted_text": "sensitive"},
                          {"redact_pii_policies": ["person_name"]},
                          {"redact_pii_return_unredacted": True}):
            with self.subTest(overrides=overrides), tempfile.TemporaryDirectory() as directory:
                audio = Path(directory) / "call.wav"
                audio.write_bytes(b"audio")
                with patch.object(transcribe, "request", side_effect=[
                    {"upload_url": "https://cdn.assemblyai.com/upload/test"},
                    {**self.completed(), **overrides},
                ]), self.assertRaises(transcribe.Failure):
                    transcribe.transcribe(audio, "test-key")

    def test_service_error_does_not_expose_error_body(self):
        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / "call.wav"
            audio.write_bytes(b"audio")
            with patch.object(transcribe, "request", side_effect=[
                {"upload_url": "https://cdn.assemblyai.com/upload/test"},
                {"id": "test-123", "status": "error", "error": "sensitive"},
            ]), self.assertRaises(transcribe.Failure) as context:
                transcribe.transcribe(audio, "test-key")
            self.assertNotIn("sensitive", str(context.exception))


if __name__ == "__main__":
    unittest.main()
