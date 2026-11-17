"""Regression checks for failed transcription and summary responses."""
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import assemblyai as aai

import transcribe


class TranscriptionTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.audio = Path(self.temp.name) / "memo.wav"
        self.audio.write_bytes(b"test audio")
        self.output = Path(self.temp.name) / "out.json"
        self.output.write_text("previous result")
        self.transcript = SimpleNamespace(
            status=aai.TranscriptStatus.completed,
            id="transcript-id",
            text="Quorvex ships Thursday. Talvanta moves to next sprint.",
            utterances=[SimpleNamespace(speaker="A", text="Quorvex ships Thursday.")],
        )
        self.choice = SimpleNamespace(
            finish_reason="stop",
            message=SimpleNamespace(content="Quorvex ships Thursday. Talvanta moves to next sprint."),
        )
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {"ASSEMBLYAI_API_KEY": "test-key"}).start()
        self.transcriber = patch("transcribe.aai.Transcriber").start().return_value
        self.transcriber.transcribe.return_value = self.transcript
        self.gateway = patch("transcribe.aai.LLMGateway").start().return_value
        self.gateway.chat.completions.create.return_value = SimpleNamespace(choices=[self.choice])

    def run_transcription(self):
        transcribe.main(str(self.audio), str(self.output))

    def test_output_contract_and_summary_input(self):
        self.run_transcription()
        result = json.loads(self.output.read_text())
        self.assertEqual(set(result), {"transcript_id", "text", "utterances", "summary"})
        self.assertEqual(result["utterances"], [{"speaker": "A", "text": "Quorvex ships Thursday."}])
        self.assertEqual(result["summary"], self.choice.message.content)
        config = self.transcriber.transcribe.call_args.kwargs["config"]
        self.assertTrue(config.speaker_labels)
        self.assertEqual(config.keyterms_prompt, transcribe.PRODUCT_NAMES)
        self.assertIsNone(config.speech_model)
        self.assertIsNone(config.word_boost)
        messages = self.gateway.chat.completions.create.call_args.kwargs["messages"]
        self.assertEqual(messages[-1], {"role": "user", "content": self.transcript.text})

    def test_transcription_failure_preserves_existing_output(self):
        self.transcript.status = aai.TranscriptStatus.error
        self.transcript.error = "Audio could not be decoded"
        with self.assertRaisesRegex(RuntimeError, "Audio could not be decoded"):
            self.run_transcription()
        self.gateway.chat.completions.create.assert_not_called()
        self.assertEqual(self.output.read_text(), "previous result")

    def test_summary_failure_preserves_existing_output(self):
        self.gateway.chat.completions.create.side_effect = aai.LLMGatewayError("Unavailable", 503)
        with self.assertRaises(aai.LLMGatewayError):
            self.run_transcription()
        self.assertEqual(self.output.read_text(), "previous result")

    def test_empty_or_truncated_summary_is_failure(self):
        for content, finish_reason in [(None, "stop"), ("", "stop"), ("Partial", "length")]:
            with self.subTest(content=content, finish_reason=finish_reason):
                self.choice.message.content = content
                self.choice.finish_reason = finish_reason
                with self.assertRaisesRegex(RuntimeError, "complete summary"):
                    self.run_transcription()
                self.assertEqual(self.output.read_text(), "previous result")


if __name__ == "__main__":
    unittest.main()
