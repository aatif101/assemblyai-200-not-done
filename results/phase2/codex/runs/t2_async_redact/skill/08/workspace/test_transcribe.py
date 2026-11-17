import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import transcribe as app


def completed():
    return {
        "id": "test-id", "status": "completed", "redact_pii": True,
        "redact_pii_policies": app.POLICIES,
        "redact_pii_return_unredacted": False,
        "text": "Hello [PERSON_NAME]. Call [PHONE_NUMBER].",
        "utterances": [
            {"speaker": "A", "text": "Hello [PERSON_NAME].", "start": 0},
            {"speaker": "B", "text": "Call [PHONE_NUMBER].", "start": 1000},
        ],
    }


class PrivacyAndFailureTests(unittest.TestCase):
    def test_upload_submission_and_polling(self):
        responses = [
            {"upload_url": "https://example.invalid/audio"},
            {"id": "test-id", "status": "queued"},
            {"id": "test-id", "status": "processing"}, completed(),
        ]
        calls = []

        def request(key, method, endpoint, **kwargs):
            calls.append((method, endpoint, kwargs))
            if endpoint == "/upload":
                self.assertEqual(kwargs["body"].read(), b"fake audio")
                self.assertEqual(kwargs["content_type"], "application/octet-stream")
                self.assertEqual(kwargs["content_length"], 10)
            return responses.pop(0)

        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / "input.wav"
            audio.write_bytes(b"fake audio")
            with patch.object(app, "request_json", side_effect=request), patch.object(app.time, "sleep"):
                result = app.transcribe("test-key", audio)
        config = json.loads(calls[1][2]["body"])
        self.assertTrue(config["redact_pii"])
        self.assertCountEqual(config["redact_pii_policies"], app.POLICIES)
        self.assertIs(config["redact_pii_return_unredacted"], False)
        self.assertTrue(config["speaker_labels"])
        self.assertEqual(config["speaker_options"],
                         {"min_speakers_expected": 2, "max_speakers_expected": 2})
        self.assertEqual([call[0] for call in calls], ["POST", "POST", "GET", "GET"])
        self.assertEqual(set(result), {"transcript_id", "text", "utterances"})
        self.assertTrue(all(set(u) == {"speaker", "text"} for u in result["utterances"]))

    def test_refuses_missing_redaction_and_missing_policy(self):
        for changes in ({"redact_pii": False}, {"redact_pii_policies": ["person_name"]}):
            with self.subTest(changes=changes), self.assertRaises(app.Failure):
                app.export_result("test-id", completed() | changes)

    def test_refuses_unredacted_fields(self):
        for field, value in (("redact_pii_return_unredacted", True),
                             ("unredacted_text", "sensitive"),
                             ("unredacted_utterances", [{"text": "sensitive"}])):
            with self.subTest(field=field), self.assertRaises(app.Failure):
                app.export_result("test-id", completed() | {field: value})

    def test_refuses_missing_speaker(self):
        result = completed()
        result["utterances"][0]["speaker"] = None
        with self.assertRaises(app.Failure):
            app.export_result("test-id", result)

    def test_provider_error_is_sanitized_and_never_deleted(self):
        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / "input.wav"
            audio.write_bytes(b"fake audio")
            with patch.object(app, "request_json", side_effect=[
                {"upload_url": "https://example.invalid/audio"},
                {"id": "test-id", "status": "error", "error": "sensitive content"},
            ]) as request:
                with self.assertRaises(app.Failure) as caught:
                    app.transcribe("test-key", audio)
                self.assertNotIn("sensitive content", str(caught.exception))
                self.assertEqual(request.call_count, 2)

    def test_atomic_private_output(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "out.json"
            destination.write_text("previous output")
            result = app.export_result("test-id", completed())
            app.write_output(destination, result)
            self.assertEqual(json.loads(destination.read_text()), result)
            self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
            self.assertEqual(list(Path(directory).iterdir()), [destination])


if __name__ == "__main__":
    unittest.main()
