import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import transcribe


class TranscriptionTests(unittest.TestCase):
    def test_submission_uploads_binary_and_requires_server_redaction(self):
        calls = []

        def fake_request(key, method, endpoint, data=None, headers=None):
            self.assertEqual(key, "test-key")
            calls.append((method, endpoint))
            if endpoint == "/upload":
                self.assertEqual(data.read(), b"RIFF sample audio")
                self.assertEqual(headers["Content-Type"], "application/octet-stream")
                return {"upload_url": "https://example.test/audio"}
            config = json.loads(data)
            self.assertTrue(config["speaker_labels"])
            self.assertEqual(config["speaker_options"],
                             {"min_speakers_expected": 2, "max_speakers_expected": 2})
            self.assertIs(config["redact_pii"], True)
            self.assertIs(config["redact_pii_return_unredacted"], False)
            self.assertEqual(set(config["redact_pii_policies"]),
                             {"person_name", "phone_number", "us_social_security_number"})
            self.assertNotIn("speech_understanding", config)
            return {"id": "test-id", "status": "queued"}

        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / "audio.wav"
            audio.write_bytes(b"RIFF sample audio")
            with patch.object(transcribe, "request_json", side_effect=fake_request):
                transcribe.submit("test-key", audio)
        self.assertEqual(calls, [("POST", "/upload"), ("POST", "/transcript")])

    def test_refuses_missing_redaction_or_unredacted_copies(self):
        safe = {"redact_pii": True, "redact_pii_policies": transcribe.POLICIES,
                "text": "Hello [PERSON_NAME]", "utterances": [
                    {"speaker": "A", "text": "Hello [PERSON_NAME]", "start": 0}]}
        for overrides in ({"redact_pii": False}, {"redact_pii_policies": []},
                          {"redact_pii_return_unredacted": True},
                          {"unredacted_utterances": [{"text": "sensitive"}]}):
            with self.subTest(overrides=overrides):
                with self.assertRaises(transcribe.TranscriptionError):
                    transcribe.output_document({**safe, **overrides}, "test-id")
        output = transcribe.output_document(safe, "test-id")
        self.assertEqual(set(output), {"transcript_id", "text", "utterances"})
        self.assertEqual(output["utterances"],
                         [{"speaker": "A", "text": "Hello [PERSON_NAME]"}])

    def test_failed_job_exits_nonzero_without_output_or_sensitive_error(self):
        with tempfile.TemporaryDirectory() as directory:
            audio, output = Path(directory) / "audio.wav", Path(directory) / "out.json"
            audio.write_bytes(b"audio")
            stderr = io.StringIO()
            with patch("sys.argv", ["transcribe.py", str(audio), str(output)]), \
                 patch.dict("os.environ", {"ASSEMBLYAI_API_KEY": "test-key"}), \
                 patch.object(transcribe, "submit", return_value={"id": "test-id"}), \
                 patch.object(transcribe, "request_json", return_value={
                     "status": "error", "error": "sensitive server error"}) as request, \
                 contextlib.redirect_stderr(stderr):
                self.assertEqual(transcribe.main(), 1)
            self.assertFalse(output.exists())
            self.assertNotIn("sensitive server error", stderr.getvalue())
            self.assertEqual(request.call_args.args[1], "GET")


if __name__ == "__main__":
    unittest.main()
