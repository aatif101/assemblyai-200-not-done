import copy
import unittest
from pathlib import Path
from unittest.mock import patch

import transcribe


class PrivacyTests(unittest.TestCase):
    def setUp(self):
        self.job = {
            "id": "test-id", "status": "completed", "redact_pii": True,
            "redact_pii_policies": transcribe.POLICIES,
            "redact_pii_return_unredacted": False,
            "text": "Hello [PERSON_NAME].",
            "utterances": [{"speaker": "A", "text": "Hello [PERSON_NAME]."}],
        }

    def run_job(self, job):
        calls = []

        def fake_request(key, method, path, data=None, *args):
            calls.append((method, path, data))
            if path == "/upload":
                self.assertEqual(data.read(4), b"RIFF")
                return {"upload_url": "https://example.invalid/audio"}
            if method == "POST" and path == "/transcript":
                import json
                config = json.loads(data)
                self.assertIs(config["redact_pii"], True)
                self.assertIs(config["redact_pii_return_unredacted"], False)
                self.assertEqual(config["redact_pii_policies"], transcribe.POLICIES)
                self.assertIs(config["speaker_labels"], True)
                self.assertEqual(config["speaker_options"], {
                    "min_speakers_expected": 2, "max_speakers_expected": 2})
                return {"id": "test-id"}
            self.assertEqual((method, path), ("GET", "/transcript/test-id"))
            return job

        with patch.object(transcribe, "request_json", side_effect=fake_request):
            result = transcribe.transcribe("test-key", Path(__file__).parent / "sample.wav")
        self.assertEqual([c[0] for c in calls], ["POST", "POST", "GET"])
        return result

    def test_request_and_output(self):
        result = self.run_job(self.job)
        self.assertEqual(set(result), {"transcript_id", "text", "utterances"})
        self.assertEqual(result["utterances"], self.job["utterances"])

    def test_reject_unsafe_response(self):
        for change in ({"redact_pii": False}, {"redact_pii_policies": []},
                       {"redact_pii_return_unredacted": True},
                       {"unredacted_utterances": []}):
            with self.subTest(change=change):
                job = copy.deepcopy(self.job)
                job.update(change)
                with self.assertRaises(transcribe.TranscriptionError):
                    self.run_job(job)

    def test_remote_error(self):
        with self.assertRaises(transcribe.TranscriptionError):
            self.run_job({"status": "error"})


if __name__ == "__main__":
    unittest.main()
