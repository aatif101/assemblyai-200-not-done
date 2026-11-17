"""Offline checks for the memo pipeline's success and failure contracts."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import assemblyai as aai

import transcribe


class MemoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.audio = Path(self.temp.name) / "memo.wav"
        self.audio.touch()
        self.output = Path(self.temp.name) / "memo.json"
        self.output.write_text("previous successful output")
        self.transcript = SimpleNamespace(
            id="transcript-123",
            status=aai.TranscriptStatus.completed,
            error=None,
            text="Quorvex ships Thursday. Talvanta moves to next sprint.",
            utterances=[
                SimpleNamespace(speaker="A", text="Quorvex ships Thursday."),
                SimpleNamespace(speaker="B", text="Talvanta moves to next sprint."),
            ],
        )
        env = patch.dict(os.environ, {"ASSEMBLYAI_API_KEY": "test-key"})
        env.start()
        self.addCleanup(env.stop)
        transcriber = patch("transcribe.aai.Transcriber")
        self.transcriber = transcriber.start().return_value
        self.addCleanup(transcriber.stop)
        self.transcriber.transcribe.return_value = self.transcript
        gateway = patch("transcribe.aai.LLMGateway")
        self.generate = gateway.start().return_value.chat.completions.create
        self.addCleanup(gateway.stop)
        self.choice = SimpleNamespace(
            finish_reason="stop",
            message=SimpleNamespace(content="Quorvex ships Thursday. Talvanta is deferred."),
        )
        self.generate.return_value = SimpleNamespace(choices=[self.choice])

    def run_memo(self):
        transcribe.main(str(self.audio), str(self.output))

    def test_success_keeps_schema_and_multiple_speakers(self):
        self.run_memo()
        result = json.loads(self.output.read_text())
        self.assertEqual(set(result), {"transcript_id", "text", "utterances", "summary"})
        self.assertEqual(result["transcript_id"], self.transcript.id)
        self.assertEqual(result["text"], self.transcript.text)
        self.assertEqual(result["utterances"], [vars(u) for u in self.transcript.utterances])
        self.assertEqual(result["summary"], self.choice.message.content)
        config = self.transcriber.transcribe.call_args.kwargs["config"]
        self.assertTrue(config.speaker_labels)
        self.assertEqual(config.keyterms_prompt, transcribe.PRODUCT_NAMES)
        self.assertIn("universal-3-5-pro", config.speech_models)
        messages = self.generate.call_args.kwargs["messages"]
        self.assertEqual(messages[-1]["content"], self.transcript.text)

    def test_transcription_error_skips_summary_and_preserves_output(self):
        self.transcript.status = aai.TranscriptStatus.error
        self.transcript.error = "Audio could not be decoded"
        with self.assertRaisesRegex(RuntimeError, "Audio could not be decoded"):
            self.run_memo()
        self.generate.assert_not_called()
        self.assertEqual(self.output.read_text(), "previous successful output")

    def test_gateway_error_preserves_output(self):
        self.generate.side_effect = aai.LLMGatewayError("Summary service unavailable")
        with self.assertRaisesRegex(aai.LLMGatewayError, "Summary service unavailable"):
            self.run_memo()
        self.assertEqual(self.output.read_text(), "previous successful output")

    def test_incomplete_summaries_fail_without_overwriting_output(self):
        for content, finish_reason in [("", "stop"), ("Partial summary", "length")]:
            with self.subTest(content=content, finish_reason=finish_reason):
                self.choice.message.content = content
                self.choice.finish_reason = finish_reason
                with self.assertRaisesRegex(RuntimeError, "incomplete or empty summary"):
                    self.run_memo()
                self.assertEqual(self.output.read_text(), "previous successful output")

    def test_silent_audio_fails_before_summary(self):
        self.transcript.text = ""
        with self.assertRaisesRegex(RuntimeError, "no speech"):
            self.run_memo()
        self.generate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
