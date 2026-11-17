"""Regression checks for the CLI's output contract and API migration."""
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
        self.transcript = SimpleNamespace(
            id="test-id", status=aai.TranscriptStatus.completed,
            text="Quorvex is ready. Zyntrix Flow and Talvanta ship Friday.",
            utterances=[
                SimpleNamespace(speaker="A", text="Quorvex is ready."),
                SimpleNamespace(speaker="B", text="Zyntrix Flow and Talvanta ship Friday."),
            ],
        )
        self.env = patch.dict(os.environ, {"ASSEMBLYAI_API_KEY": "test-key"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.stt = patch.object(transcribe.aai, "Transcriber").start()
        self.gateway = patch.object(transcribe.aai, "LLMGateway").start()
        self.addCleanup(patch.stopall)
        self.stt.return_value.transcribe.return_value = self.transcript
        self.create = self.gateway.return_value.chat.completions.create
        self.create.return_value = SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content="Quorvex is ready. Other products ship Friday."),
            finish_reason="stop",
        )])

    def test_current_request_and_preserved_output(self):
        transcribe.main("memo.wav", str(self.output))
        config = self.stt.return_value.transcribe.call_args.kwargs["config"]
        payload = json.loads(config.raw.json(exclude_none=True))
        self.assertEqual(payload, {
            "speech_models": ["universal-3-5-pro"], "speaker_labels": True,
            "keyterms_prompt": transcribe.PRODUCT_NAMES,
        })
        request = self.create.call_args.kwargs
        self.assertEqual(request["messages"][1]["content"], self.transcript.text)
        self.assertIn("two sentences", request["messages"][0]["content"])
        self.assertNotIn("transcript_ids", request)
        self.assertEqual(json.loads(self.output.read_text()), {
            "transcript_id": "test-id", "text": self.transcript.text,
            "utterances": [vars(u) for u in self.transcript.utterances],
            "summary": "Quorvex is ready. Other products ship Friday.",
        })

    def test_transcription_failure_does_not_write_or_summarize(self):
        self.transcript.status = aai.TranscriptStatus.error
        self.transcript.error = "bad audio"
        with self.assertRaisesRegex(RuntimeError, "bad audio"):
            transcribe.main("memo.wav", str(self.output))
        self.create.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_gateway_failure_preserves_existing_output(self):
        self.output.write_text("previous result")
        self.create.side_effect = RuntimeError("gateway unavailable")
        with self.assertRaisesRegex(RuntimeError, "gateway unavailable"):
            transcribe.main("memo.wav", str(self.output))
        self.assertEqual(self.output.read_text(), "previous result")

    def test_empty_summary_is_failure(self):
        self.create.return_value.choices[0].message.content = None
        with self.assertRaisesRegex(RuntimeError, "no text"):
            transcribe.main("memo.wav", str(self.output))
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
