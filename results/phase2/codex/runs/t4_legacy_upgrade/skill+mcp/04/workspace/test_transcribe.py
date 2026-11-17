"""Regression checks for the memo output contract and API failure handling."""
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import assemblyai as aai

import transcribe


class MemoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.out = Path(self.temp.name) / "out.json"
        self.env = patch.dict(os.environ, {"ASSEMBLYAI_API_KEY": "test-key"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.stt = patch.object(transcribe.aai, "Transcriber").start()
        self.gateway = patch.object(transcribe.aai, "LLMGateway").start()
        self.addCleanup(patch.stopall)
        self.stt.return_value.transcribe.return_value = SimpleNamespace(
            id="memo-id", status=aai.TranscriptStatus.completed,
            text="Quorvex and Zyntrix Flow launch Friday. Talvanta follows Monday.",
            utterances=[SimpleNamespace(speaker="A", text="Quorvex launches Friday."),
                        SimpleNamespace(speaker="B", text="Talvanta follows Monday.")],
            error=None,
        )
        self.create = self.gateway.return_value.chat.completions.create
        self.create.return_value = SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content="Quorvex launches Friday. Talvanta follows Monday.")
        )])

    def test_memo_contract_and_current_api_parameters(self):
        transcribe.main("memo.wav", str(self.out))
        result = json.loads(self.out.read_text())
        self.assertEqual(set(result), {"transcript_id", "text", "utterances", "summary"})
        self.assertEqual(result["transcript_id"], "memo-id")
        self.assertEqual([u["speaker"] for u in result["utterances"]], ["A", "B"])
        self.assertTrue(all(set(u) == {"speaker", "text"} for u in result["utterances"]))
        config = self.stt.return_value.transcribe.call_args.kwargs["config"]
        self.assertEqual(config.speech_models, ["universal-3-5-pro", "universal-2"])
        self.assertTrue(config.speaker_labels)
        self.assertEqual(config.keyterms_prompt, transcribe.PRODUCT_NAMES)
        self.assertIsNone(config.word_boost)
        self.assertIsNone(config.boost_param)
        messages = self.create.call_args.kwargs["messages"]
        self.assertEqual(messages[-1]["content"], result["text"])
        self.assertIn("two", messages[0]["content"])

    def test_transcription_failure_skips_summary_and_preserves_output(self):
        self.out.write_text("previous output")
        transcript = self.stt.return_value.transcribe.return_value
        transcript.status = aai.TranscriptStatus.error
        transcript.error = "Audio decode failed"
        with self.assertRaisesRegex(RuntimeError, "Audio decode failed"):
            transcribe.main("memo.wav", str(self.out))
        self.create.assert_not_called()
        self.assertEqual(self.out.read_text(), "previous output")

    def test_gateway_failure_does_not_write_success_output(self):
        self.create.side_effect = RuntimeError("Summary service unavailable")
        with self.assertRaisesRegex(RuntimeError, "Summary service unavailable"):
            transcribe.main("memo.wav", str(self.out))
        self.assertFalse(self.out.exists())

    def test_empty_summary_is_failure(self):
        self.create.return_value.choices[0].message.content = None
        with self.assertRaisesRegex(RuntimeError, "no text"):
            transcribe.main("memo.wav", str(self.out))
        self.assertFalse(self.out.exists())

    def test_missing_key_fails_before_api_calls(self):
        os.environ["ASSEMBLYAI_API_KEY"] = ""
        with self.assertRaisesRegex(RuntimeError, "ASSEMBLYAI_API_KEY is required"):
            transcribe.main("memo.wav", str(self.out))
        self.stt.assert_not_called()


if __name__ == "__main__":
    unittest.main()
