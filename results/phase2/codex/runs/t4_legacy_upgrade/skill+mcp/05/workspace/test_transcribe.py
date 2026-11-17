"""Offline regression checks. Run with python -m unittest -v."""
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
        self.output.write_text("existing output", encoding="utf-8")
        self.env = patch.dict(os.environ, {"ASSEMBLYAI_API_KEY": "test-key"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.transcriber = patch.object(transcribe.aai, "Transcriber").start()
        self.gateway = patch.object(transcribe.aai, "LLMGateway").start()
        self.addCleanup(patch.stopall)
        self.transcriber.return_value.transcribe.return_value = SimpleNamespace(
            status=aai.TranscriptStatus.completed,
            id="test-transcript",
            text="Quorvex ships Friday. Talvanta testing continues.",
            utterances=[SimpleNamespace(speaker="A", text="Quorvex ships Friday.")],
        )
        self.create = self.gateway.return_value.chat.completions.create
        self.choice = SimpleNamespace(
            message=SimpleNamespace(content="Quorvex ships Friday. Talvanta testing continues."),
            finish_reason="stop",
        )
        self.create.return_value = SimpleNamespace(choices=[self.choice])

    def test_output_contract_and_summary_source(self):
        transcribe.main("memo.wav", str(self.output))
        result = json.loads(self.output.read_text(encoding="utf-8"))
        self.assertEqual(result, {
            "transcript_id": "test-transcript",
            "text": "Quorvex ships Friday. Talvanta testing continues.",
            "utterances": [{"speaker": "A", "text": "Quorvex ships Friday."}],
            "summary": "Quorvex ships Friday. Talvanta testing continues.",
        })
        messages = self.create.call_args.kwargs["messages"]
        self.assertEqual(messages[-1], {"role": "user", "content": result["text"]})

    def test_failed_transcription_does_not_summarize_or_overwrite(self):
        self.transcriber.return_value.transcribe.return_value = SimpleNamespace(
            status=aai.TranscriptStatus.error, error="Audio decoding failed"
        )
        with self.assertRaisesRegex(RuntimeError, "Audio decoding failed"):
            transcribe.main("memo.wav", str(self.output))
        self.create.assert_not_called()
        self.assertEqual(self.output.read_text(), "existing output")

    def test_summary_failure_preserves_existing_output(self):
        for content in (None, "", "   "):
            with self.subTest(content=content):
                self.choice.message.content = content
                with self.assertRaisesRegex(RuntimeError, "no text"):
                    transcribe.main("memo.wav", str(self.output))
                self.assertEqual(self.output.read_text(), "existing output")

    def test_summary_api_failure_propagates(self):
        self.create.side_effect = RuntimeError("Gateway unavailable")
        with self.assertRaisesRegex(RuntimeError, "Gateway unavailable"):
            transcribe.main("memo.wav", str(self.output))
        self.assertEqual(self.output.read_text(), "existing output")

    def test_truncated_summary_fails(self):
        self.choice.finish_reason = "length"
        with self.assertRaisesRegex(RuntimeError, "token limit"):
            transcribe.main("memo.wav", str(self.output))

    def test_missing_api_key_fails_before_network(self):
        del os.environ["ASSEMBLYAI_API_KEY"]
        with self.assertRaisesRegex(RuntimeError, "ASSEMBLYAI_API_KEY"):
            transcribe.main("memo.wav", str(self.output))
        self.transcriber.assert_not_called()


if __name__ == "__main__":
    unittest.main()
