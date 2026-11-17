import json
from pathlib import Path
import unittest

import httpx

from transcribe import Failure, POLICIES, transcribe


class TranscriptionTests(unittest.TestCase):
    def run_job(self, overrides=None):
        def handler(request):
            self.assertNotEqual(request.method, "DELETE")
            if request.url.path.endswith("/upload"):
                return httpx.Response(200, json={"upload_url": "https://example.test/audio"})
            if request.method == "POST":
                config = json.loads(request.content)
                self.assertTrue(config["speaker_labels"])
                self.assertEqual(config["speakers_expected"], 2)
                self.assertTrue(config["redact_pii"])
                self.assertFalse(config["redact_pii_return_unredacted"])
                self.assertEqual(set(config["redact_pii_policies"]), set(POLICIES))
                return httpx.Response(200, json={"id": "test-id"})
            result = {
                "status": "completed", "redact_pii": True,
                "redact_pii_policies": POLICIES,
                "text": "Hello [PERSON_NAME]",
                "utterances": [{"speaker": "A", "text": "Hello [PERSON_NAME]", "words": []}],
            }
            result.update(overrides or {})
            return httpx.Response(200, json=result)
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            return transcribe(client, Path(__file__).parent / "sample.wav")

    def test_redacted_output_schema(self):
        self.assertEqual(self.run_job(), {
            "transcript_id": "test-id", "text": "Hello [PERSON_NAME]",
            "utterances": [{"speaker": "A", "text": "Hello [PERSON_NAME]"}],
        })

    def test_fail_closed(self):
        for override in [
            {"redact_pii": False}, {"redact_pii_policies": ["person_name"]},
            {"unredacted_text": "private"}, {"redact_pii_return_unredacted": True},
            {"status": "error", "error": "private error text"},
            {"utterances": [{"speaker": None, "text": "hello"}]},
        ]:
            with self.subTest(override=override), self.assertRaises(Failure):
                self.run_job(override)


if __name__ == "__main__":
    unittest.main()
