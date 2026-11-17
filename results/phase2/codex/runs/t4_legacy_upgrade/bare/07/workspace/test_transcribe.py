"""Offline checks for the API migration and the scheduler's output contract."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import assemblyai as aai
import transcribe


class TranscribeTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.output = Path(self.directory.name) / "out.json"
        self.transcriber = self.enterContext(patch("transcribe.aai.Transcriber"))
        self.gateway = self.enterContext(patch("transcribe.aai.LLMGateway"))
        self.enterContext(patch.dict("os.environ", {"ASSEMBLYAI_API_KEY": "test-key"}))
        self.transcript = SimpleNamespace(
            id="transcript-id", status=aai.TranscriptStatus.completed, error=None,
            text="Quorvex ships Thursday. Talvanta moves to next sprint.",
            utterances=[
                SimpleNamespace(speaker="A", text="Quorvex ships Thursday."),
                SimpleNamespace(speaker="B", text="Talvanta moves to next sprint."),
            ],
        )
        self.transcriber.return_value.transcribe.return_value = self.transcript
        self.complete = self.gateway.return_value.chat.completions.create
        self.complete.return_value = SimpleNamespace(choices=[SimpleNamespace(
            finish_reason="stop", message=SimpleNamespace(content=self.transcript.text)
        )])

    def test_current_api_options_and_output_contract(self):
        transcribe.main("memo.wav", str(self.output))
        config = self.transcriber.return_value.transcribe.call_args.kwargs["config"]
        self.assertEqual(config.speech_models, ["universal-3-pro"])
        self.assertTrue(config.speaker_labels)
        self.assertEqual(config.keyterms_prompt, transcribe.PRODUCT_NAMES)
        self.assertFalse(config.word_boost)
        self.assertEqual(self.complete.call_args.kwargs["messages"][-1]["content"],
                         self.transcript.text)
        self.assertEqual(json.loads(self.output.read_text()), {
            "transcript_id": "transcript-id", "text": self.transcript.text,
            "utterances": [{"speaker": u.speaker, "text": u.text}
                           for u in self.transcript.utterances],
            "summary": self.transcript.text,
        })

    def test_transcription_error_skips_summary_and_output(self):
        self.transcript.status = aai.TranscriptStatus.error
        self.transcript.error = "Invalid audio"
        with self.assertRaisesRegex(RuntimeError, "Invalid audio"):
            transcribe.main("memo.wav", str(self.output))
        self.complete.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_summary_failure_preserves_existing_output(self):
        self.output.write_text("previous result")
        self.complete.side_effect = RuntimeError("Gateway unavailable")
        with self.assertRaisesRegex(RuntimeError, "Gateway unavailable"):
            transcribe.main("memo.wav", str(self.output))
        self.assertEqual(self.output.read_text(), "previous result")

    def test_empty_or_truncated_summary_is_failure(self):
        for content, finish_reason in [("", "stop"), ("Partial", "length")]:
            with self.subTest(content=content, finish_reason=finish_reason):
                self.complete.return_value.choices = [SimpleNamespace(
                    finish_reason=finish_reason, message=SimpleNamespace(content=content)
                )]
                with self.assertRaises(RuntimeError):
                    transcribe.main("memo.wav", str(self.output))
                self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
