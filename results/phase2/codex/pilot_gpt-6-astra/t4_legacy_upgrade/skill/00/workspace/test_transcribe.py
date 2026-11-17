"""Offline regression checks: python -m unittest -v."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch, Mock

import requests
import transcribe


class MemoTests(unittest.TestCase):
    def test_success_preserves_output_contract(self):
        transcript = SimpleNamespace(
            status=transcribe.aai.TranscriptStatus.completed,
            id="memo-id", text="Quorvex is ready.",
            utterances=[SimpleNamespace(speaker="A", text="Quorvex is ready.")],
        )
        response = Mock()
        response.json.return_value = {"choices": [{
            "finish_reason": "stop", "message": {"content": "Quorvex is ready. Launch is next week."}
        }]}
        with tempfile.TemporaryDirectory() as directory, \
                patch.dict("os.environ", {"ASSEMBLYAI_API_KEY": "test-key"}), \
                patch.object(transcribe.aai, "Transcriber") as client, \
                patch.object(transcribe.requests, "post", return_value=response) as post:
            client.return_value.transcribe.return_value = transcript
            output = Path(directory) / "memo.json"
            transcribe.main("memo.wav", str(output))
            result = json.loads(output.read_text())
            self.assertEqual(set(result), {"transcript_id", "text", "utterances", "summary"})
            self.assertEqual(result["utterances"], [{"speaker": "A", "text": transcript.text}])
            self.assertEqual(post.call_args.kwargs["json"]["messages"][1]["content"], transcript.text)
            self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "test-key")

    def test_transcription_failure_does_not_summarize(self):
        with patch.dict("os.environ", {"ASSEMBLYAI_API_KEY": "test-key"}), \
                patch.object(transcribe.aai, "Transcriber") as client, \
                patch.object(transcribe, "summarize") as summarize:
            client.return_value.transcribe.return_value = SimpleNamespace(
                status=transcribe.aai.TranscriptStatus.error, error="Invalid audio"
            )
            with self.assertRaisesRegex(RuntimeError, "Invalid audio"):
                transcribe.main("bad.wav", "unused.json")
            summarize.assert_not_called()

    def test_summary_http_failure_propagates(self):
        with patch.object(transcribe.requests, "post") as post:
            post.return_value.raise_for_status.side_effect = requests.HTTPError("Unavailable")
            with self.assertRaises(requests.HTTPError):
                transcribe.summarize("Memo", "test-key")

    def test_empty_or_truncated_summary_fails(self):
        for content, reason in [(None, "stop"), ("", "stop"), ("Partial", "length")]:
            with self.subTest(content=content, reason=reason), \
                    patch.object(transcribe.requests, "post") as post:
                post.return_value.json.return_value = {"choices": [{
                    "finish_reason": reason, "message": {"content": content}
                }]}
                with self.assertRaises(RuntimeError):
                    transcribe.summarize("Memo", "test-key")


if __name__ == "__main__":
    unittest.main()
