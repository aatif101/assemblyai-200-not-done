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
        self.key = patch.dict(os.environ, {"ASSEMBLYAI_API_KEY": "test-key"})
        self.key.start()
        self.addCleanup(self.key.stop)

    def transcript(self, **overrides):
        fields = dict(
            id="transcript-123",
            status=transcribe.aai.TranscriptStatus.completed,
            text="Quorvex launches Friday. Talvanta is in testing.",
            utterances=[SimpleNamespace(speaker="A", text="Quorvex launches Friday.")],
        )
        fields.update(overrides)
        return SimpleNamespace(**fields)

    @patch("transcribe.httpx.post")
    @patch("transcribe.aai.Transcriber")
    def test_current_api_and_output_contract(self, client, post):
        transcript = self.transcript()
        client.return_value.transcribe.return_value = transcript
        summary = "Quorvex launches Friday. Talvanta is in testing."
        post.return_value = httpx.Response(200, json={"choices": [{
            "finish_reason": "stop", "message": {"content": summary},
        }]})
        transcribe.main("memo.wav", str(self.output))
        config = client.return_value.transcribe.call_args.kwargs["config"]
        self.assertEqual(config.speech_models, ["universal-3-5-pro"])
        self.assertTrue(config.speaker_labels)
        self.assertEqual(config.keyterms_prompt, transcribe.PRODUCT_NAMES)
        self.assertFalse(config.word_boost)
        client.assert_called_once_with(api_key="test-key")
        request = post.call_args.kwargs
        self.assertEqual(request["headers"]["Authorization"], "test-key")
        self.assertIn(transcript.text, request["json"]["messages"][1]["content"])
        self.assertEqual(json.loads(self.output.read_text()), {
            "transcript_id": transcript.id,
            "text": transcript.text,
            "utterances": [{"speaker": "A", "text": "Quorvex launches Friday."}],
            "summary": summary,
        })

    @patch("transcribe.httpx.post")
    @patch("transcribe.aai.Transcriber")
    def test_failed_transcription_does_not_summarize_or_write(self, client, post):
        client.return_value.transcribe.return_value = self.transcript(
            status=transcribe.aai.TranscriptStatus.error, error="Invalid audio",
        )
        with self.assertRaisesRegex(RuntimeError, "Invalid audio"):
            transcribe.main("memo.wav", str(self.output))
        post.assert_not_called()
        self.assertFalse(self.output.exists())

    @patch("transcribe.httpx.post")
    @patch("transcribe.aai.Transcriber")
    def test_failed_summary_preserves_existing_output(self, client, post):
        client.return_value.transcribe.return_value = self.transcript()
        self.output.write_text("existing output")
        for response in (
            httpx.Response(403, text="Access denied"),
            httpx.Response(200, json={"choices": [{"message": {"content": None}}]}),
            httpx.Response(200, json={"choices": [{
                "finish_reason": "length", "message": {"content": "Partial"},
            }]}),
        ):
            with self.subTest(response=response):
                post.return_value = response
                with self.assertRaises(RuntimeError):
                    transcribe.main("memo.wav", str(self.output))
                self.assertEqual(self.output.read_text(), "existing output")


if __name__ == "__main__":
    unittest.main()
