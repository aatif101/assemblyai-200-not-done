"""Offline regression tests: python -m unittest -v."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import assemblyai as aai

import transcribe


class TranscribeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name) / "out.json"
        self.output.write_text("previous output", encoding="utf-8")
        self.transcriber = self.enterContext(patch.object(aai, "Transcriber"))
        self.gateway = self.enterContext(patch.object(aai, "LLMGateway"))
        self.transcript = SimpleNamespace(
            id="memo-id", status=aai.TranscriptStatus.completed,
            text="Quorvex ships Thursday. Talvanta follows next sprint.",
            utterances=[
                SimpleNamespace(speaker="A", text="Quorvex ships Thursday."),
                SimpleNamespace(speaker="B", text="Talvanta follows next sprint."),
            ],
        )
        self.transcriber.return_value.transcribe.return_value = self.transcript
        self.complete = self.gateway.return_value.chat.completions.create
        self.choice = SimpleNamespace(
            message=SimpleNamespace(content="Quorvex ships Thursday. Talvanta follows."),
            finish_reason="stop",
        )
        self.complete.return_value = SimpleNamespace(choices=[self.choice])
        self.enterContext(patch.dict("os.environ", {"ASSEMBLYAI_API_KEY": "test-key"}))

    def test_current_api_and_output_contract(self):
        transcribe.main("memo.wav", str(self.output))
        config = self.transcriber.return_value.transcribe.call_args.kwargs["config"]
        request = config.raw.dict(exclude_none=True)
        self.assertEqual(request, {
            "speech_models": ["universal-3-5-pro"], "speaker_labels": True,
            "keyterms_prompt": transcribe.PRODUCT_NAMES,
        })
        messages = self.complete.call_args.kwargs["messages"]
        self.assertEqual(messages[-1]["content"], self.transcript.text)
        self.assertIn("two sentences", messages[0]["content"])
        self.assertEqual(json.loads(self.output.read_text()), {
            "transcript_id": "memo-id", "text": self.transcript.text,
            "utterances": [{"speaker": u.speaker, "text": u.text}
                           for u in self.transcript.utterances],
            "summary": self.choice.message.content,
        })

    def test_transcription_error_does_not_summarize_or_replace_output(self):
        self.transcript.status = aai.TranscriptStatus.error
        self.transcript.error = "bad audio"
        with self.assertRaisesRegex(RuntimeError, "bad audio"):
            transcribe.main("memo.wav", str(self.output))
        self.complete.assert_not_called()
        self.assertEqual(self.output.read_text(), "previous output")

    def test_summary_errors_preserve_previous_output(self):
        for content, reason in [("", "stop"), (None, "stop"), ("Partial", "length")]:
            with self.subTest(content=content, reason=reason):
                self.choice.message.content = content
                self.choice.finish_reason = reason
                with self.assertRaisesRegex(RuntimeError, "Summarization failed"):
                    transcribe.main("memo.wav", str(self.output))
                self.assertEqual(self.output.read_text(), "previous output")

    def test_gateway_failure_preserves_previous_output(self):
        self.complete.side_effect = aai.LLMGatewayError("Service unavailable")
        with self.assertRaises(aai.LLMGatewayError):
            transcribe.main("memo.wav", str(self.output))
        self.assertEqual(self.output.read_text(), "previous output")


if __name__ == "__main__":
    unittest.main()
