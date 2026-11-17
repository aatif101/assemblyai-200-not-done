import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import transcribe


class TranscriptionTests(unittest.TestCase):
    def completed(self):
        return {
            "id": "test-id", "status": "completed", "redact_pii": True,
            "redact_pii_policies": transcribe.POLICIES,
            "text": "Hello [PERSON_NAME]. [PHONE_NUMBER] [US_SOCIAL_SECURITY_NUMBER]",
            "utterances": [
                {"speaker": "A", "text": "Hello [PERSON_NAME].", "start": 0},
                {"speaker": "B", "text": "[PHONE_NUMBER] [US_SOCIAL_SECURITY_NUMBER]"},
            ],
        }

    def run_job(self, result):
        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / "call.wav"
            audio.write_bytes(b"audio")
            with patch.object(transcribe, "request_json", side_effect=[
                {"upload_url": "https://cdn.assemblyai.com/test"},
                {"id": "test-id"}, result,
            ]) as request:
                output = transcribe.transcribe(audio, "test-key")
                calls = request.call_args_list
        return output, calls

    def test_server_redaction_diarization_and_retention(self):
        output, calls = self.run_job(self.completed())
        self.assertEqual([call.args[0] for call in calls], ["POST", "POST", "GET"])
        config = json.loads(calls[1].args[3])
        self.assertIs(config["redact_pii"], True)
        self.assertIs(config["redact_pii_return_unredacted"], False)
        self.assertEqual(set(config["redact_pii_policies"]), set(transcribe.POLICIES))
        self.assertIs(config["speaker_labels"], True)
        self.assertEqual(config["speaker_options"],
                         {"min_speakers_expected": 2, "max_speakers_expected": 2})
        self.assertEqual(set(output), {"transcript_id", "text", "utterances"})
        self.assertEqual(output["transcript_id"], "test-id")
        self.assertEqual([u["speaker"] for u in output["utterances"]], ["A", "B"])
        for utterance in output["utterances"]:
            self.assertEqual(set(utterance), {"speaker", "text"})

    def test_rejects_unconfirmed_or_unredacted_result(self):
        for changes in ({"redact_pii": False}, {"redact_pii_policies": []},
                        {"redact_pii_return_unredacted": True},
                        {"unredacted_utterances": [{"text": "sensitive"}]}):
            with self.subTest(changes=changes):
                job = self.completed()
                job.update(changes)
                with self.assertRaises(transcribe.TranscriptionError):
                    self.run_job(job)

    def test_failed_job_does_not_expose_error_body(self):
        with self.assertRaises(transcribe.TranscriptionError) as caught:
            self.run_job({"status": "error", "error": "sensitive server error"})
        self.assertNotIn("sensitive", str(caught.exception))

    def test_output_is_exact_and_private(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "out.json"
            result = {"transcript_id": "test-id", "text": "", "utterances": []}
            transcribe.write_output(output, result)
            self.assertEqual(json.loads(output.read_text()), result)
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            self.assertEqual(list(Path(directory).iterdir()), [output])


if __name__ == "__main__":
    unittest.main()
