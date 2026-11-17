"""Offline regression checks for the API migration: python -m unittest."""
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import assemblyai as aai

import transcribe


class TranscribeTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name) / "out.json"
        self.enterContext(patch.dict(os.environ, {"ASSEMBLYAI_API_KEY": "test-key"}))
        self.transcriber = self.enterContext(patch.object(aai, "Transcriber"))
        self.gateway = self.enterContext(patch.object(aai, "LLMGateway"))
        self.transcriber.return_value.transcribe.return_value = SimpleNamespace(
            id="test-id",
            status=aai.TranscriptStatus.completed,
            text="Quorvex ships Thursday. Talvanta moves to next sprint.",
            utterances=[
                SimpleNamespace(speaker="A", text="Quorvex ships Thursday."),
                SimpleNamespace(speaker="B", text="Talvanta moves to next sprint."),
            ],
        )
        self.complete = self.gateway.return_value.chat.completions.create
        self.complete.return_value = SimpleNamespace(choices=[SimpleNamespace(
            finish_reason="stop",
            message=SimpleNamespace(content="Quorvex ships Thursday. Talvanta is delayed."),
        )])

    def test_success_preserves_output_and_sends_current_api_options(self):
        transcribe.main("memo.wav", str(self.output))
        result = json.loads(self.output.read_text())
        self.assertEqual(set(result), {"transcript_id", "text", "utterances", "summary"})
        self.assertEqual(result["transcript_id"], "test-id")
        self.assertEqual(result["utterances"], [
            {"speaker": "A", "text": "Quorvex ships Thursday."},
            {"speaker": "B", "text": "Talvanta moves to next sprint."},
        ])
        self.assertEqual(result["summary"], "Quorvex ships Thursday. Talvanta is delayed.")
        config = self.transcriber.return_value.transcribe.call_args.kwargs["config"].raw
        self.assertEqual(config.speech_models, ["universal-3-pro"])
        self.assertTrue(config.speaker_labels)
        self.assertEqual(config.keyterms_prompt, transcribe.PRODUCT_NAMES)
        self.assertIsNone(config.word_boost)
        self.assertIsNone(config.boost_param)
        self.assertIsNone(config.speech_model)
        messages = self.complete.call_args.kwargs["messages"]
        self.assertEqual(messages[-1], {"role": "user", "content": result["text"]})

    def test_transcription_error_stops_before_summary(self):
        self.transcriber.return_value.transcribe.return_value = SimpleNamespace(
            status=aai.TranscriptStatus.error, error="Invalid audio",
        )
        with self.assertRaisesRegex(RuntimeError, "Invalid audio"):
            transcribe.main("bad.wav", str(self.output))
        self.complete.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_summary_api_error_preserves_existing_output(self):
        self.output.write_text("previous output")
        self.complete.side_effect = aai.LLMGatewayError("Unavailable", status_code=503)
        with self.assertRaises(aai.LLMGatewayError):
            transcribe.main("memo.wav", str(self.output))
        self.assertEqual(self.output.read_text(), "previous output")

    def test_empty_or_truncated_summary_is_failure(self):
        for choices in [[], [SimpleNamespace(
            finish_reason="stop", message=SimpleNamespace(content=" "),
        )], [SimpleNamespace(
            finish_reason="length", message=SimpleNamespace(content="Partial"),
        )]]:
            with self.subTest(choices=choices):
                self.complete.return_value = SimpleNamespace(choices=choices)
                with self.assertRaises(RuntimeError):
                    transcribe.main("memo.wav", str(self.output))
                self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
