import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import transcribe


def completed():
    return {
        "status": "completed",
        "redact_pii": True,
        "redact_pii_policies": transcribe.POLICIES,
        "text": "Hello [PERSON_NAME].",
        "utterances": [{"speaker": "A", "text": "Hello [PERSON_NAME].", "start": 0}],
    }


class TranscriptionTests(unittest.TestCase):
    def test_submission_redacts_at_provider_and_never_deletes(self):
        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / "call.wav"
            audio.write_bytes(b"test audio")
            with patch.object(transcribe, "api_request", side_effect=[
                {"upload_url": "https://example.com/audio"}, {"id": "test-id"},
                {"status": "processing"}, completed(),
            ]) as api, patch.object(transcribe.time, "sleep"):
                result = transcribe.transcribe("test-key", audio)
        config = json.loads(api.call_args_list[1].args[3])
        self.assertIs(config["redact_pii"], True)
        self.assertEqual(set(config["redact_pii_policies"]), set(transcribe.POLICIES))
        self.assertIs(config["redact_pii_return_unredacted"], False)
        self.assertIs(config["speaker_labels"], True)
        self.assertEqual(config["speaker_options"],
                         {"min_speakers_expected": 2, "max_speakers_expected": 2})
        self.assertEqual([call.args[1] for call in api.call_args_list],
                         ["POST", "POST", "GET", "GET"])
        self.assertEqual(result, {"transcript_id": "test-id", "text": "Hello [PERSON_NAME].",
                                 "utterances": [{"speaker": "A", "text": "Hello [PERSON_NAME]."}]})

    def test_rejects_unconfirmed_or_unredacted_response(self):
        for changes in ({"redact_pii": False}, {"redact_pii_policies": ["person_name"]},
                        {"redact_pii_return_unredacted": True},
                        {"unredacted_text": "sensitive"}):
            with self.subTest(changes=changes), self.assertRaises(transcribe.TranscriptionError):
                transcribe.format_result("test-id", completed() | changes)

    def test_rejects_missing_diarization(self):
        with self.assertRaises(transcribe.TranscriptionError):
            transcribe.format_result("test-id", completed() | {"utterances": None})

    def test_failure_preserves_output_and_removes_temporary_file(self):
        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / "call.wav"
            audio.write_bytes(b"test audio")
            output = Path(directory) / "out.json"
            output.write_text("previous output")
            with patch.dict(os.environ, {"ASSEMBLYAI_API_KEY": "test-key"}), \
                    patch.object(transcribe.sys, "argv", ["run.sh", str(audio), str(output)]), \
                    patch.object(transcribe, "transcribe", side_effect=transcribe.TranscriptionError("Failed")):
                self.assertNotEqual(transcribe.main(), 0)
            self.assertEqual(output.read_text(), "previous output")
            self.assertEqual(list(Path(directory).glob(".transcript-*")), [])


if __name__ == "__main__":
    unittest.main()
