"""Regression checks for the current AssemblyAI request and output contracts."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import transcribe


class TranscribeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.output = Path(self.directory.name) / "out.json"
        self.transcriber = self.enterContext(patch.object(transcribe.aai, "Transcriber"))
        self.gateway = self.enterContext(patch.object(transcribe.aai, "LLMGateway"))
        self.enterContext(patch.dict(os.environ, {"ASSEMBLYAI_API_KEY": "test-key"}))
        self.transcriber.return_value.transcribe.return_value = SimpleNamespace(
            status=transcribe.aai.TranscriptStatus.completed,
            id="transcript-id",
            text="Quorvex ships Thursday. Zyntrix Flow and Talvanta follow.",
            utterances=[
                SimpleNamespace(speaker="A", text="Quorvex ships Thursday."),
                SimpleNamespace(speaker="B", text="Zyntrix Flow and Talvanta follow."),
            ],
        )
        self.summary = self.gateway.return_value.chat.completions.create
        self.summary.return_value = SimpleNamespace(choices=[SimpleNamespace(
            finish_reason="stop",
            message=SimpleNamespace(content="Quorvex ships Thursday. Other products follow."),
        )])

    def test_request_and_output_contract(self):
        transcribe.main("memo.wav", str(self.output))
        config = self.transcriber.return_value.transcribe.call_args.kwargs["config"]
        self.assertEqual(config.raw.dict(exclude_none=True), {
            "speech_models": ["universal-3-pro"],
            "speaker_labels": True,
            "keyterms_prompt": transcribe.PRODUCT_NAMES,
        })
        result = json.loads(self.output.read_text())
        self.assertEqual(set(result), {"transcript_id", "text", "utterances", "summary"})
        self.assertEqual(result["transcript_id"], "transcript-id")
        self.assertEqual(result["utterances"], [
            {"speaker": "A", "text": "Quorvex ships Thursday."},
            {"speaker": "B", "text": "Zyntrix Flow and Talvanta follow."},
        ])
        self.assertEqual(result["summary"], "Quorvex ships Thursday. Other products follow.")
        messages = self.summary.call_args.kwargs["messages"]
        self.assertEqual(messages[-1]["role"], "user")
        self.assertIn(result["text"], messages[-1]["content"])

    def test_transcription_failure_stops_before_summary(self):
        self.transcriber.return_value.transcribe.return_value = SimpleNamespace(
            status=transcribe.aai.TranscriptStatus.error, error="Audio could not be decoded",
        )
        with self.assertRaisesRegex(RuntimeError, "Audio could not be decoded"):
            transcribe.main("memo.wav", str(self.output))
        self.summary.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_summary_failure_preserves_existing_output(self):
        self.output.write_text("previous output")
        self.summary.side_effect = RuntimeError("Gateway unavailable")
        with self.assertRaisesRegex(RuntimeError, "Gateway unavailable"):
            transcribe.main("memo.wav", str(self.output))
        self.assertEqual(self.output.read_text(), "previous output")

    def test_incomplete_summary_is_failure(self):
        self.summary.return_value.choices[0].finish_reason = "length"
        with self.assertRaisesRegex(RuntimeError, "did not complete"):
            transcribe.main("memo.wav", str(self.output))
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
