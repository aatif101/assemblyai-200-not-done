"""Regression tests for API configuration and all-or-nothing output."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

import assemblyai as aai
import transcribe


class TranscribeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.audio = Path(self.directory.name) / "memo.wav"
        self.audio.touch()
        self.output = Path(self.directory.name) / "memo.json"
        self.output.write_text("previous result")
        self.transcript = NS(
            id="test-id", status=aai.TranscriptStatus.completed, error=None,
            text="Quorvex ships Thursday.",
            utterances=[NS(speaker="A", text="Quorvex ships Thursday.")],
        )
        self.completion = NS(choices=[NS(
            finish_reason="stop", message=NS(content="Quorvex ships Thursday. Update the notes.")
        )])

    def run_memo(self, transcript=None, completion=None, summary_error=None):
        with patch.dict(os.environ, ASSEMBLYAI_API_KEY="test-key"), \
             patch.object(aai, "Transcriber") as stt, \
             patch.object(aai, "LLMGateway") as gateway:
            stt.return_value.transcribe.return_value = transcript or self.transcript
            create = gateway.return_value.chat.completions.create
            create.return_value = completion or self.completion
            create.side_effect = summary_error
            transcribe.main(str(self.audio), str(self.output))
            return stt, create

    def test_current_api_and_unchanged_schema(self):
        stt, create = self.run_memo()
        config = stt.return_value.transcribe.call_args.kwargs["config"].raw.dict(exclude_none=True)
        self.assertEqual(config["speech_models"], ["universal-3-pro"])
        self.assertTrue(config["speaker_labels"])
        self.assertEqual(config["keyterms_prompt"], transcribe.PRODUCT_NAMES)
        self.assertNotIn("speech_model", config)
        self.assertNotIn("word_boost", config)
        self.assertNotIn("boost_param", config)
        self.assertEqual(create.call_args.kwargs["messages"][1]["content"], self.transcript.text)
        self.assertEqual(json.loads(self.output.read_text()), {
            "transcript_id": "test-id", "text": self.transcript.text,
            "utterances": [{"speaker": "A", "text": self.transcript.text}],
            "summary": "Quorvex ships Thursday. Update the notes.",
        })

    def test_failed_transcript_preserves_output(self):
        self.transcript.status = aai.TranscriptStatus.error
        self.transcript.error = "service failure"
        with self.assertRaisesRegex(RuntimeError, "service failure"):
            self.run_memo()
        self.assertEqual(self.output.read_text(), "previous result")

    def test_summary_errors_preserve_output(self):
        for completion, error in [
            (NS(choices=[]), None),
            (NS(choices=[NS(finish_reason="length")]), None),
            (NS(choices=[NS(finish_reason="stop", message=NS(content=""))]), None),
            (None, RuntimeError("gateway unavailable")),
        ]:
            with self.subTest(completion=completion, error=error):
                with self.assertRaises(RuntimeError):
                    self.run_memo(completion=completion, summary_error=error)
                self.assertEqual(self.output.read_text(), "previous result")


if __name__ == "__main__":
    unittest.main()
