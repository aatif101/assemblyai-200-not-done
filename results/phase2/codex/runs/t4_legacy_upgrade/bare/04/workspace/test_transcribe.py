"""Run offline regression checks with: python -m unittest -v."""
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import assemblyai as aai
import transcribe


class MemoTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.output = Path(self.directory.name) / "out.json"
        self.output.write_text("previous result", encoding="utf-8")
        self.transcript = SimpleNamespace(
            id="memo-id", status=aai.TranscriptStatus.completed, error=None,
            text="Quorvex ships Thursday. Talvanta moves to next sprint.",
            utterances=[
                SimpleNamespace(speaker="A", text="Quorvex ships Thursday."),
                SimpleNamespace(speaker="B", text="Talvanta moves to next sprint."),
            ],
        )
        self.summary = "Quorvex ships Thursday. Talvanta moves to next sprint."
        self.choice = SimpleNamespace(
            finish_reason="stop", message=SimpleNamespace(content=self.summary),
        )
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {"ASSEMBLYAI_API_KEY": "test-key"}).start()
        self.transcriber = patch("transcribe.aai.Transcriber").start()
        self.transcriber.return_value.transcribe.return_value = self.transcript
        self.gateway = patch("transcribe.aai.LLMGateway").start()
        self.complete = self.gateway.return_value.chat.completions.create
        self.complete.return_value = SimpleNamespace(choices=[self.choice])

    def test_success_preserves_output_contract_and_speakers(self):
        transcribe.main("memo.wav", str(self.output))
        self.assertEqual(json.loads(self.output.read_text()), {
            "transcript_id": "memo-id", "text": self.transcript.text,
            "utterances": [
                {"speaker": "A", "text": "Quorvex ships Thursday."},
                {"speaker": "B", "text": "Talvanta moves to next sprint."},
            ],
            "summary": self.summary,
        })
        # The summary request must contain this recording's transcript.
        messages = self.complete.call_args.kwargs["messages"]
        self.assertTrue(any(m["content"] == self.transcript.text for m in messages))

    def test_transcript_error_does_not_summarize_or_replace_output(self):
        self.transcript.status = aai.TranscriptStatus.error
        self.transcript.error = "Invalid audio"
        with self.assertRaisesRegex(RuntimeError, "Invalid audio"):
            transcribe.main("memo.wav", str(self.output))
        self.gateway.assert_not_called()
        self.assertEqual(self.output.read_text(), "previous result")

    def test_gateway_failure_preserves_output(self):
        self.complete.side_effect = RuntimeError("Gateway unavailable")
        with self.assertRaisesRegex(RuntimeError, "Gateway unavailable"):
            transcribe.main("memo.wav", str(self.output))
        self.assertEqual(self.output.read_text(), "previous result")

    def test_empty_or_truncated_summary_is_a_failure(self):
        for content, finish_reason in [("", "stop"), ("Partial", "length")]:
            with self.subTest(content=content):
                self.choice.message.content = content
                self.choice.finish_reason = finish_reason
                with self.assertRaises(RuntimeError):
                    transcribe.main("memo.wav", str(self.output))
                self.assertEqual(self.output.read_text(), "previous result")


if __name__ == "__main__":
    unittest.main()
