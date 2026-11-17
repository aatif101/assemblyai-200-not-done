import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import transcribe as app


class TranscriptionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.audio = Path(self.directory.name) / "audio.wav"
        self.audio.write_bytes(b"test audio")
        self.completed = {
            "id": "test-id", "status": "completed", "redact_pii": True,
            "redact_pii_policies": app.POLICIES.copy(),
            "text": "Hello [PERSON_NAME].",
            "utterances": [{"speaker": "A", "text": "Hello [PERSON_NAME].",
                            "words": [{"text": "irrelevant metadata"}]}],
        }

    def run_job(self, completed):
        return patch.object(app, "request", side_effect=[
            {"upload_url": "https://cdn.assemblyai.com/test"},
            {"id": "test-id", "status": "queued"}, completed,
        ])

    @patch.object(app.time, "sleep")
    def test_privacy_submission_and_output_contract(self, sleep):
        with self.run_job(self.completed) as request:
            result = app.transcribe("secret", self.audio)
        payload = request.call_args_list[1].kwargs["payload"]
        self.assertIs(payload["redact_pii"], True)
        self.assertIs(payload["redact_pii_return_unredacted"], False)
        self.assertEqual(set(payload["redact_pii_policies"]), set(app.POLICIES))
        self.assertIs(payload["speaker_labels"], True)
        self.assertEqual(payload["speakers_expected"], 2)
        self.assertEqual([c.args[1] for c in request.call_args_list],
                         ["/upload", "/transcript", "/transcript/test-id"])
        self.assertEqual(result, {
            "transcript_id": "test-id", "text": "Hello [PERSON_NAME].",
            "utterances": [{"speaker": "A", "text": "Hello [PERSON_NAME]."}],
        })
        output = self.audio.parent / "out.json"
        app.write_output(output, result)
        self.assertEqual(json.loads(output.read_text()), result)
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)
        self.assertEqual(list(self.audio.parent.glob(".transcript-*")), [])

    @patch.object(app.time, "sleep")
    def test_rejects_missing_redaction_and_originals(self, sleep):
        changes = [
            {"redact_pii": False},
            {"redact_pii_policies": ["person_name"]},
            {"redact_pii_return_unredacted": True},
            {"unredacted_text": "sensitive"},
            {"unredacted_words": [{"text": "sensitive"}]},
            {"unredacted_utterances": [{"text": "sensitive"}]},
            {"utterances": None},
            {"utterances": [{"speaker": None, "text": "text"}]},
        ]
        for change in changes:
            with self.subTest(change=list(change)), self.run_job(self.completed | change):
                with self.assertRaises(app.TranscriptionError):
                    app.transcribe("secret", self.audio)

    @patch.object(app.time, "sleep")
    def test_service_error_does_not_expose_response_text(self, sleep):
        with self.run_job({"status": "error", "error": "sensitive service content"}):
            with self.assertRaises(app.TranscriptionError) as raised:
                app.transcribe("secret", self.audio)
        self.assertNotIn("sensitive", str(raised.exception))

    @patch.object(app, "MAX_WAIT_SECONDS", 0)
    def test_timeout_preserves_remote_job(self):
        with self.run_job(self.completed) as request:
            with self.assertRaisesRegex(app.TranscriptionError, "Timed out"):
                app.transcribe("secret", self.audio)
        self.assertEqual(request.call_count, 2)


if __name__ == "__main__":
    unittest.main()
