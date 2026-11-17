"""Privacy and failure-path checks without submitting additional calls."""

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import transcribe


class PrivacyTests(unittest.TestCase):
    def result(self, **overrides):
        result = {
            "id": "test-id", "status": "completed",
            "redact_pii": True, "redact_pii_policies": transcribe.POLICIES,
            "text": "Hello [PERSON_NAME].", "utterances": [
                {"speaker": "A", "text": "Hello [PERSON_NAME].", "start": 0}
            ],
        }
        result.update(overrides)
        return result

    def run_response(self, response):
        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / "call.wav"
            audio.write_bytes(b"sample audio")
            with patch.object(transcribe, "request", side_effect=[{"upload_url": "https://example.com/audio"}, response]) as request:
                with contextlib.redirect_stderr(io.StringIO()):
                    result = transcribe.transcribe("test-key", audio)
                return result, request.call_args_list

    def test_redaction_on_submission_and_exact_export(self):
        import json
        result, calls = self.run_response(self.result())
        config = json.loads(calls[1].args[3])
        self.assertTrue(config["redact_pii"])
        self.assertIs(config["redact_pii_return_unredacted"], False)
        self.assertEqual(set(config["redact_pii_policies"]), set(transcribe.POLICIES))
        self.assertEqual(config["speaker_options"], {"min_speakers_expected": 2, "max_speakers_expected": 2})
        self.assertEqual([call.args[1] for call in calls], ["POST", "POST"])
        self.assertEqual(set(result), {"transcript_id", "text", "utterances"})
        self.assertEqual(result["utterances"], [{"speaker": "A", "text": "Hello [PERSON_NAME]."}])

    def test_rejects_unredacted_data_and_missing_policies(self):
        for overrides in (
            {"redact_pii": False},
            {"redact_pii_policies": ["person_name"]},
            {"redact_pii_return_unredacted": True},
            {"unredacted_utterances": [{"text": "sensitive"}]},
            {"utterances": [{"text": "missing speaker"}]},
            {"status": "error", "error": "potentially sensitive server error"},
        ):
            with self.subTest(overrides=overrides):
                with self.assertRaises(transcribe.TranscriptionError) as caught:
                    self.run_response(self.result(**overrides))
                self.assertNotIn("potentially sensitive", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
