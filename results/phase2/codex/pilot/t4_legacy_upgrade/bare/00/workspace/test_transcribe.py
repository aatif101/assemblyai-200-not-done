"""Regression checks for the API migration and scheduler output contract."""
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

import assemblyai as aai
import transcribe


class TranscribeTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.output = Path(self.directory.name) / "out.json"
        self.env = patch.dict(os.environ, {"ASSEMBLYAI_API_KEY": "test-key"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.transcriber = patch.object(transcribe.aai, "Transcriber").start()
        self.gateway = patch.object(transcribe.aai, "LLMGateway").start()
        self.addCleanup(patch.stopall)
        self.transcriber.return_value.transcribe.return_value = NS(
            status=aai.TranscriptStatus.completed, id="test-id", error=None,
            text="Quorvex is ready. Talvanta is next.",
            utterances=[NS(speaker="A", text="Quorvex is ready."),
                        NS(speaker="B", text="Talvanta is next.")],
        )
        self.create = self.gateway.return_value.chat.completions.create
        self.create.return_value = NS(choices=[NS(
            finish_reason="stop", message=NS(content="Quorvex is ready. Talvanta is next.")
        )])

    def test_output_and_current_api_configuration(self):
        transcribe.main("memo.wav", str(self.output))
        out = json.loads(self.output.read_text())
        self.assertEqual(set(out), {"transcript_id", "text", "utterances", "summary"})
        self.assertEqual(out["utterances"], [
            {"speaker": "A", "text": "Quorvex is ready."},
            {"speaker": "B", "text": "Talvanta is next."},
        ])
        config = self.transcriber.return_value.transcribe.call_args.kwargs["config"]
        self.assertEqual(config.speech_models, ["universal-3-pro"])
        self.assertTrue(config.speaker_labels)
        self.assertEqual(config.keyterms_prompt, transcribe.PRODUCT_NAMES)
        self.assertFalse(config.word_boost)
        messages = self.create.call_args.kwargs["messages"]
        self.assertEqual(messages[1]["content"], out["text"])
        self.assertEqual(out["summary"], "Quorvex is ready. Talvanta is next.")

    def test_transcription_failure_does_not_summarize_or_overwrite(self):
        self.output.write_text("previous result")
        transcript = self.transcriber.return_value.transcribe.return_value
        transcript.status = aai.TranscriptStatus.error
        transcript.error = "Audio could not be decoded"
        with self.assertRaisesRegex(RuntimeError, "Audio could not be decoded"):
            transcribe.main("memo.wav", str(self.output))
        self.gateway.assert_not_called()
        self.assertEqual(self.output.read_text(), "previous result")

    def test_summary_failures_do_not_write_success_output(self):
        for completion in [NS(choices=[]), NS(choices=[NS(
            finish_reason="stop", message=NS(content="")
        )]), NS(choices=[NS(finish_reason="length", message=NS(content="Partial"))])]:
            with self.subTest(completion=completion):
                self.create.return_value = completion
                with self.assertRaisesRegex(RuntimeError, "Summary failed"):
                    transcribe.main("memo.wav", str(self.output))
                self.assertFalse(self.output.exists())

    def test_missing_key_fails_before_api_calls(self):
        os.environ.pop("ASSEMBLYAI_API_KEY")
        with self.assertRaisesRegex(ValueError, "ASSEMBLYAI_API_KEY"):
            transcribe.main("memo.wav", str(self.output))
        self.transcriber.assert_not_called()


if __name__ == "__main__":
    unittest.main()
