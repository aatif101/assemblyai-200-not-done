import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import transcribe as t


class TranscriptionTests(unittest.TestCase):
    def setUp(self):
        self.result = {
            "id": "test-id", "status": "completed",
            "redact_pii": True, "redact_pii_policies": t.POLICIES,
            "text": "Hello [PERSON_NAME].",
            "utterances": [{"speaker": "B", "text": "Hello [PERSON_NAME].", "start": 0}],
        }

    def test_omitted_false_flag_and_exact_output_schema(self):
        record = t.output_record(self.result, "test-id")
        self.assertEqual(record, {
            "transcript_id": "test-id", "text": "Hello [PERSON_NAME].",
            "utterances": [{"speaker": "B", "text": "Hello [PERSON_NAME]."}],
        })

    def test_rejects_unsafe_or_incomplete_results(self):
        for change in [
            {"redact_pii": False},
            {"redact_pii_policies": ["person_name"]},
            {"redact_pii_return_unredacted": True},
            {"unredacted_text": "sensitive"},
            {"utterances": [{"speaker": None, "text": "Hello"}]},
        ]:
            with self.subTest(change=change):
                result = copy.deepcopy(self.result)
                result.update(change)
                with self.assertRaises(t.TranscriptionError):
                    t.output_record(result, "test-id")

    def test_submission_and_polling_keep_redaction_and_do_not_delete(self):
        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / "in.wav"
            output = Path(directory) / "out.json"
            audio.write_bytes(b"test audio")
            with patch.dict(t.os.environ, {"ASSEMBLYAI_API_KEY": "test-key"}), \
                 patch.object(t.sys, "argv", ["run.sh", str(audio), str(output)]), \
                 patch.object(t.time, "sleep"), \
                 patch.object(t, "request", side_effect=[
                     {"upload_url": "https://example.test/audio"},
                     {"id": "test-id", "status": "queued"}, self.result,
                 ]) as request:
                t.main()
            calls = request.call_args_list
            self.assertEqual([c.args[1] for c in calls], ["POST", "POST", "GET"])
            config = calls[1].kwargs["payload"]
            self.assertIs(config["redact_pii_return_unredacted"], False)
            self.assertIs(config["redact_pii"], True)
            self.assertEqual(set(config["redact_pii_policies"]), set(t.POLICIES))
            self.assertIs(config["speaker_labels"], True)
            self.assertEqual(config["speaker_options"], {
                "min_speakers_expected": 2, "max_speakers_expected": 2,
            })
            self.assertEqual(json.loads(output.read_text())["transcript_id"], "test-id")
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
