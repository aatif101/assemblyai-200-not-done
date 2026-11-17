import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import transcribe


class TranscriptionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.audio = Path(self.directory.name) / "audio.wav"
        self.audio.write_bytes(b"test audio")
        self.completed = {
            "id": "test-id", "status": "completed",
            "redact_pii": True, "redact_pii_policies": transcribe.POLICIES,
            "text": "Hello [PERSON_NAME].",
            "utterances": [{"speaker": "A", "text": "Hello [PERSON_NAME].", "words": []}],
        }

    def run_response(self, response):
        with patch.object(transcribe, "request", side_effect=[
            {"upload_url": "https://example.invalid/audio"},
            {"id": "test-id", "status": "queued"}, response,
        ]) as request, patch.object(transcribe.time, "sleep"):
            result = transcribe.transcribe("test-key", self.audio)
        return result, request

    def test_submission_and_output_privacy_contract(self):
        result, request = self.run_response(self.completed)
        config = json.loads(request.call_args_list[1].args[3])
        self.assertTrue(config["redact_pii"])
        self.assertFalse(config["redact_pii_return_unredacted"])
        self.assertEqual(set(config["redact_pii_policies"]), {
            "person_name", "phone_number", "us_social_security_number"
        })
        self.assertTrue(config["speaker_labels"])
        self.assertEqual(config["speakers_expected"], 2)
        self.assertEqual([c.args[1] for c in request.call_args_list], ["POST", "POST", "GET"])
        self.assertEqual(result, {
            "transcript_id": "test-id", "text": "Hello [PERSON_NAME].",
            "utterances": [{"speaker": "A", "text": "Hello [PERSON_NAME]."}],
        })

    def test_reject_missing_redaction_or_unredacted_fields(self):
        for changes in [
            {"redact_pii": False},
            {"redact_pii_policies": ["person_name"]},
            {"redact_pii_return_unredacted": True},
            {"unredacted_text": "sensitive"},
            {"unredacted_utterances": []},
        ]:
            with self.subTest(changes=changes):
                response = copy.deepcopy(self.completed)
                response.update(changes)
                with self.assertRaises(transcribe.TranscriptionError):
                    self.run_response(response)

    def test_remote_error_does_not_expose_response_details(self):
        with self.assertRaises(transcribe.TranscriptionError) as caught:
            self.run_response({"status": "error", "error": "sensitive customer data"})
        self.assertNotIn("sensitive", str(caught.exception))

    def test_failed_atomic_write_preserves_previous_output(self):
        destination = Path(self.directory.name) / "out.json"
        destination.write_text("previous output")
        with patch.object(transcribe.os, "replace", side_effect=OSError("disk error")):
            with self.assertRaises(OSError):
                transcribe.write_output(destination, {"text": "[PERSON_NAME]"})
        self.assertEqual(destination.read_text(), "previous output")
        self.assertEqual(list(destination.parent.glob(".transcript-*")), [])


if __name__ == "__main__":
    unittest.main()
