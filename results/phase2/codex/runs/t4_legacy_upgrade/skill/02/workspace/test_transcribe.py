"""Offline regression tests; run with python -m unittest -v."""
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx

import transcribe


class TranscribeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.output = Path(self.directory.name) / "out.json"
        self.transcript = SimpleNamespace(
            id="test-transcript",
            status=transcribe.aai.TranscriptStatus.completed,
            text="Quorvex integrates with Zyntrix Flow and Talvanta.",
            utterances=[SimpleNamespace(speaker="A", text="Quorvex is ready.")],
        )

    @patch.dict(os.environ, {"ASSEMBLYAI_API_KEY": "test-key"})
    @patch("transcribe.httpx.post")
    @patch("transcribe.aai.Transcriber")
    def test_current_api_and_original_output(self, client, post):
        client.return_value.transcribe.return_value = self.transcript
        post.return_value = httpx.Response(
            200,
            json={"choices": [{"finish_reason": "stop", "message": {
                "content": "Quorvex is ready. The integrations are complete."
            }}]},
        )

        transcribe.main("memo.wav", str(self.output))

        config = client.return_value.transcribe.call_args.kwargs["config"]
        request = config.raw.dict(exclude_none=True)
        self.assertEqual(request["speech_models"], ["universal-3-5-pro"])
        self.assertTrue(request["speaker_labels"])
        self.assertEqual(request["keyterms_prompt"], transcribe.PRODUCT_NAMES)
        self.assertFalse({"word_boost", "boost_param", "speech_model"} & request.keys())
        self.assertEqual(post.call_args.kwargs["headers"], {"Authorization": "test-key"})
        self.assertEqual(
            post.call_args.kwargs["json"]["messages"][-1]["content"],
            self.transcript.text,
        )
        self.assertEqual(json.loads(self.output.read_text()), {
            "transcript_id": "test-transcript",
            "text": self.transcript.text,
            "utterances": [{"speaker": "A", "text": "Quorvex is ready."}],
            "summary": "Quorvex is ready. The integrations are complete.",
        })

    @patch.dict(os.environ, {"ASSEMBLYAI_API_KEY": "test-key"})
    @patch("transcribe.httpx.post")
    @patch("transcribe.aai.Transcriber")
    def test_transcription_failure_does_not_summarize_or_write(self, client, post):
        self.transcript.status = transcribe.aai.TranscriptStatus.error
        self.transcript.error = "Invalid audio"
        client.return_value.transcribe.return_value = self.transcript
        with self.assertRaisesRegex(RuntimeError, "Transcription failed: Invalid audio"):
            transcribe.main("memo.wav", str(self.output))
        post.assert_not_called()
        self.assertFalse(self.output.exists())

    @patch.dict(os.environ, {"ASSEMBLYAI_API_KEY": "test-key"})
    @patch("transcribe.httpx.post")
    @patch("transcribe.aai.Transcriber")
    def test_summary_failure_preserves_existing_output(self, client, post):
        client.return_value.transcribe.return_value = self.transcript
        self.output.write_text("previous output")
        post.return_value = httpx.Response(403, json={"error": "Access denied"})
        with self.assertRaisesRegex(RuntimeError, "Summary request failed.*403"):
            transcribe.main("memo.wav", str(self.output))
        self.assertEqual(self.output.read_text(), "previous output")

    @patch("transcribe.httpx.post")
    def test_empty_or_truncated_summary_is_failure(self, post):
        for reason, content in [("stop", None), ("stop", " "), ("length", "Partial")]:
            with self.subTest(reason=reason, content=content):
                post.return_value = httpx.Response(200, json={"choices": [{
                    "finish_reason": reason, "message": {"content": content}
                }]})
                with self.assertRaises(RuntimeError):
                    transcribe.summarize("A memo", "test-key")


if __name__ == "__main__":
    unittest.main()
