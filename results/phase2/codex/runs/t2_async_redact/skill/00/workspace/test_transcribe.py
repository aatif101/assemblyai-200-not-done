import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import transcribe


class RunnerTests(unittest.TestCase):
    def test_upload_and_privacy_configuration(self):
        completed = {
            "id": "test-id", "status": "completed", "redact_pii": True,
            "redact_pii_policies": transcribe.PII_POLICIES,
            "text": "Hello [PERSON_NAME].", "utterances": [
                {"speaker": "A", "text": "Hello [PERSON_NAME].", "start": 0}
            ],
        }
        with tempfile.TemporaryDirectory() as folder:
            audio = Path(folder) / "audio.wav"
            audio.write_bytes(b"RIFF-test-audio")

            def respond(key, method, path, data=None, *args):
                self.assertEqual(key, "test-key")
                if path == "/upload":
                    self.assertEqual(data.read(), b"RIFF-test-audio")
                    return {"upload_url": "https://example.invalid/upload"}
                self.assertEqual((method, path), ("POST", "/transcript"))
                config = json.loads(data)
                self.assertTrue(config["redact_pii"])
                self.assertFalse(config["redact_pii_return_unredacted"])
                self.assertEqual(config["redact_pii_policies"], transcribe.PII_POLICIES)
                self.assertTrue(config["speaker_labels"])
                self.assertEqual(config["speaker_options"], {
                    "min_speakers_expected": 2, "max_speakers_expected": 2})
                return completed

            with patch.object(transcribe, "request_json", side_effect=respond), patch("sys.stderr", io.StringIO()):
                result = transcribe.transcribe("test-key", audio)
            self.assertEqual(set(result), {"transcript_id", "text", "utterances"})
            self.assertEqual(result["utterances"], [{"speaker": "A", "text": "Hello [PERSON_NAME]."}])

    def test_rejects_missing_redaction_confirmation(self):
        responses = [{"upload_url": "https://example.invalid/upload"},
                     {"id": "test-id", "status": "completed", "text": "unsafe"}]
        with tempfile.TemporaryDirectory() as folder:
            audio = Path(folder) / "audio.wav"
            audio.write_bytes(b"audio")
            with patch.object(transcribe, "request_json", side_effect=responses), patch("sys.stderr", io.StringIO()):
                with self.assertRaises(transcribe.TranscriptionError):
                    transcribe.transcribe("test-key", audio)

    def test_missing_key_preserves_existing_output(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "out.json"
            output.write_text("existing")
            with patch.dict("os.environ", {}, clear=True), patch("sys.argv", ["run.sh", "sample.wav", str(output)]), patch("sys.stderr", io.StringIO()):
                self.assertNotEqual(transcribe.main(), 0)
            self.assertEqual(output.read_text(), "existing")


if __name__ == "__main__":
    unittest.main()
