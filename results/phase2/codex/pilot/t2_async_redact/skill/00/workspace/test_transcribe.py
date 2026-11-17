import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import transcribe


class TranscribeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.audio = Path(self.directory.name) / "input.wav"
        self.audio.write_bytes(b"sample audio bytes")
        self.completed = {
            "id": "test-id", "status": "completed",
            "redact_pii": True, "redact_pii_policies": transcribe.POLICIES,
            "text": "Hello [PERSON_NAME]. Call [PHONE_NUMBER].",
            "utterances": [
                {"speaker": "A", "text": "Hello [PERSON_NAME].", "start": 0},
                {"speaker": "B", "text": "Call [PHONE_NUMBER].", "start": 100},
            ],
        }

    def run_job(self, completed):
        self.calls = []

        def fake_request(key, method, path, data=None, headers=None):
            self.calls.append((method, path))
            self.assertEqual(key, "test-key")
            if path == "/upload":
                self.assertEqual(data.read(), self.audio.read_bytes())
                self.assertEqual(headers["Content-Type"], "application/octet-stream")
                return {"upload_url": "https://example.invalid/audio"}
            if method == "POST":
                config = json.loads(data)
                self.assertTrue(config["redact_pii"])
                self.assertFalse(config["redact_pii_return_unredacted"])
                self.assertEqual(set(config["redact_pii_policies"]), {
                    "person_name", "phone_number", "us_social_security_number"})
                self.assertTrue(config["speaker_labels"])
                self.assertEqual(config["speaker_options"], {
                    "min_speakers_expected": 2, "max_speakers_expected": 2})
                return {"id": "test-id", "status": "queued"}
            return completed

        with patch.object(transcribe, "request", side_effect=fake_request), \
                patch.object(transcribe.time, "sleep"):
            return transcribe.transcribe("test-key", self.audio)

    def test_redacted_job_and_exact_output_schema(self):
        result = self.run_job(self.completed)
        self.assertEqual(set(result), {"transcript_id", "text", "utterances"})
        self.assertEqual(result["transcript_id"], "test-id")
        self.assertEqual(result["text"], self.completed["text"])
        self.assertEqual([u["speaker"] for u in result["utterances"]], ["A", "B"])
        for utterance in result["utterances"]:
            self.assertEqual(set(utterance), {"speaker", "text"})
        self.assertEqual(self.calls, [
            ("POST", "/upload"), ("POST", "/transcript"),
            ("GET", "/transcript/test-id")])
        output = Path(self.directory.name) / "output.json"
        transcribe.write_output(output, result)
        self.assertEqual(json.loads(output.read_text()), result)
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)

    def test_refuse_missing_redaction_or_diarization(self):
        for field, value in (
            ("redact_pii", False),
            ("redact_pii_policies", ["person_name"]),
            ("redact_pii_return_unredacted", True),
            ("unredacted_text", "sensitive customer content"),
            ("utterances", None),
        ):
            with self.subTest(field=field):
                result = copy.deepcopy(self.completed)
                result[field] = value
                with self.assertRaises(transcribe.TranscriptionError):
                    self.run_job(result)
                self.assertTrue(all(method != "DELETE" for method, _ in self.calls))

    def test_failed_job_does_not_expose_error_body_or_delete(self):
        with self.assertRaises(transcribe.TranscriptionError) as error:
            self.run_job({"status": "error", "error": "sensitive customer content"})
        self.assertNotIn("sensitive customer content", str(error.exception))
        self.assertTrue(all(method != "DELETE" for method, _ in self.calls))


if __name__ == "__main__":
    unittest.main()
