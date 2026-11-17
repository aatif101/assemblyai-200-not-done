import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import transcribe


class TranscriptionTests(unittest.TestCase):
    def test_redaction_is_requested_on_server_and_only_safe_fields_are_saved(self):
        completed = {
            "id": "test-id", "status": "completed",
            "text": "Hello [PERSON_NAME]",
            "utterances": [{"speaker": "A", "text": "Hello [PERSON_NAME]", "words": []}],
            "unredacted_text": "must never be serialized",
        }
        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / "audio.wav"
            audio.write_bytes(b"audio bytes")
            with patch.object(transcribe, "request", side_effect=[
                {"upload_url": "https://example.test/audio"},
                {"id": "test-id", "status": "queued"}, completed,
            ]) as request, patch.object(transcribe.time, "sleep"), contextlib.redirect_stderr(io.StringIO()):
                result = transcribe.transcribe("test-key", audio)
            calls = request.call_args_list
            config = json.loads(calls[1].args[3])
            self.assertTrue(config["redact_pii"])
            self.assertFalse(config["redact_pii_return_unredacted"])
            self.assertEqual(set(config["redact_pii_policies"]), {
                "person_name", "phone_number", "us_social_security_number"
            })
            self.assertTrue(config["speaker_labels"])
            self.assertEqual(config["speakers_expected"], 2)
            self.assertEqual([call.args[1] for call in calls], ["POST", "POST", "GET"])
            self.assertEqual(result, {
                "transcript_id": "test-id", "text": "Hello [PERSON_NAME]",
                "utterances": [{"speaker": "A", "text": "Hello [PERSON_NAME]"}],
            })
            output = Path(directory) / "out.json"
            transcribe.write_output(output, result)
            self.assertEqual(json.loads(output.read_text()), result)

    def test_failed_transcript_exits_nonzero_without_output_or_sensitive_error(self):
        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / "audio.wav"
            output = Path(directory) / "out.json"
            audio.write_bytes(b"audio bytes")
            diagnostics = io.StringIO()
            with patch.dict(transcribe.os.environ, {"ASSEMBLYAI_API_KEY": "test-key"}), patch.object(
                transcribe.sys, "argv", ["transcribe.py", str(audio), str(output)]
            ), patch.object(transcribe, "request", side_effect=[
                {"upload_url": "https://example.test/audio"},
                {"id": "test-id", "status": "error", "error": "sensitive server error"},
            ]) as request, contextlib.redirect_stderr(diagnostics):
                self.assertNotEqual(transcribe.main(), 0)
            self.assertFalse(output.exists())
            self.assertNotIn("sensitive server error", diagnostics.getvalue())
            self.assertNotIn("DELETE", [call.args[1] for call in request.call_args_list])


if __name__ == "__main__":
    unittest.main()
