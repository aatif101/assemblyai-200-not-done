import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import transcribe


class TranscriptionTests(unittest.TestCase):
    def setUp(self):
        self.result = {
            "id": "test-id", "status": "completed", "redact_pii": True,
            "redact_pii_policies": transcribe.POLICIES,
            "redact_pii_return_unredacted": False, "speaker_labels": True,
            "text": "Hello [PERSON_NAME]. [PHONE_NUMBER] [US_SOCIAL_SECURITY_NUMBER]",
            "utterances": [{"speaker": "A", "text": "Hello [PERSON_NAME]."},
                           {"speaker": "B", "text": "[PHONE_NUMBER] [US_SOCIAL_SECURITY_NUMBER]"}],
        }

    def test_full_flow_privacy_configuration_and_exact_output(self):
        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / "call.wav"
            output = Path(directory) / "out.json"
            audio.write_bytes(b"fake audio bytes")
            calls = []

            def api(method, path, key, body=None, content_type=None, length=None):
                calls.append((method, path))
                self.assertEqual(key, "test-key")
                if path == "/v2/upload":
                    self.assertEqual(body.read(), audio.read_bytes())
                    self.assertEqual(length, audio.stat().st_size)
                    return {"upload_url": "https://example.invalid/audio"}
                if method == "POST":
                    config = json.loads(body)
                    self.assertTrue(config["redact_pii"])
                    self.assertFalse(config["redact_pii_return_unredacted"])
                    self.assertEqual(set(config["redact_pii_policies"]), set(transcribe.POLICIES))
                    self.assertEqual(config["speaker_options"],
                                     {"min_speakers_expected": 2, "max_speakers_expected": 2})
                    return {"id": "test-id"}
                return self.result

            with patch.dict(os.environ, {"ASSEMBLYAI_API_KEY": "test-key"}), \
                    patch("sys.argv", ["transcribe.py", str(audio), str(output)]), \
                    patch.object(transcribe, "request", side_effect=api):
                transcribe.main()
            saved = json.loads(output.read_text())
            self.assertEqual(set(saved), {"transcript_id", "text", "utterances"})
            self.assertEqual(saved["text"], self.result["text"])
            self.assertEqual(saved["utterances"], self.result["utterances"])
            self.assertEqual(calls, [("POST", "/v2/upload"), ("POST", "/v2/transcript"),
                                     ("GET", "/v2/transcript/test-id")])

    def test_rejects_unredacted_or_unconfirmed_settings(self):
        for updates in ({"redact_pii": False}, {"redact_pii_policies": ["person_name"]},
                        {"unredacted_text": "sensitive"}, {"redact_pii_return_unredacted": True},
                        {"speaker_labels": False}, {"utterances": None}):
            with self.subTest(updates=updates):
                result = copy.deepcopy(self.result)
                result.update(updates)
                with self.assertRaises(transcribe.TranscriptionError):
                    transcribe.output_record(result)

    def test_api_failure_does_not_write_output(self):
        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / "call.wav"
            output = Path(directory) / "out.json"
            audio.write_bytes(b"audio")
            with patch.dict(os.environ, {"ASSEMBLYAI_API_KEY": "test-key"}), \
                    patch("sys.argv", ["transcribe.py", str(audio), str(output)]), \
                    patch.object(transcribe, "request", side_effect=[
                        {"upload_url": "https://example.invalid/audio"}, {"id": "test-id"},
                        {"id": "test-id", "status": "error", "error": "sensitive text"}]):
                with self.assertRaises(transcribe.TranscriptionError) as raised:
                    transcribe.main()
            self.assertNotIn("sensitive text", str(raised.exception))
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
