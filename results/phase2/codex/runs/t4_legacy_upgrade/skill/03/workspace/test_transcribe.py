"""Offline regression checks: python -m unittest -v."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import assemblyai as aai
import httpx

import transcribe


class MemoTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.audio = Path(self.directory.name) / "memo.wav"
        self.audio.write_bytes(b"audio")
        self.output = Path(self.directory.name) / "out.json"
        self.output.write_text("previous memo", encoding="utf-8")
        self.transcript = SimpleNamespace(
            id="test-id", status=aai.TranscriptStatus.completed,
            text="Quorvex is ready. Zyntrix Flow and Talvanta ship Friday.",
            utterances=[
                SimpleNamespace(speaker="A", text="Quorvex is ready."),
                SimpleNamespace(speaker="B", text="Zyntrix Flow and Talvanta ship Friday."),
            ],
        )
        self.enterContext(patch.dict(os.environ, ASSEMBLYAI_API_KEY="test-key"))
        self.client = self.enterContext(patch("transcribe.aai.Transcriber"))
        self.client.return_value.transcribe.return_value = self.transcript
        self.post = self.enterContext(patch("transcribe.httpx.post"))
        self.reply({"choices": [{"message": {"content": self.transcript.text},
                                  "finish_reason": "end_turn"}]})

    def reply(self, data, status=200):
        self.post.return_value = httpx.Response(
            status, json=data,
            request=httpx.Request("POST", "https://llm-gateway.assemblyai.com/v1/chat/completions"),
        )

    def run_memo(self):
        transcribe.main(str(self.audio), str(self.output))

    def test_complete_memo_preserves_schema_speakers_and_names(self):
        self.run_memo()
        result = json.loads(self.output.read_text())
        self.assertEqual(set(result), {"transcript_id", "text", "utterances", "summary"})
        self.assertEqual(result["transcript_id"], "test-id")
        self.assertEqual(result["text"], self.transcript.text)
        self.assertEqual(result["summary"], self.transcript.text)
        self.assertEqual(result["utterances"], [vars(u) for u in self.transcript.utterances])
        config = self.client.return_value.transcribe.call_args.kwargs["config"]
        self.assertEqual(config.raw.dict(exclude_none=True), {
            "speaker_labels": True, "speech_models": ["universal-3-5-pro"],
            "keyterms_prompt": transcribe.PRODUCT_NAMES,
        })
        request = self.post.call_args.kwargs
        self.assertEqual(request["headers"]["Authorization"], "test-key")
        self.assertEqual(request["json"]["messages"][-1]["content"], self.transcript.text)

    def test_transcription_error_does_not_summarize_or_overwrite(self):
        self.transcript.status = aai.TranscriptStatus.error
        self.transcript.error = "bad audio"
        with self.assertRaisesRegex(RuntimeError, "bad audio"):
            self.run_memo()
        self.post.assert_not_called()
        self.assertEqual(self.output.read_text(), "previous memo")

    def test_summary_http_failure_does_not_overwrite(self):
        self.reply({"error": "unavailable"}, status=503)
        with self.assertRaises(httpx.HTTPStatusError):
            self.run_memo()
        self.assertEqual(self.output.read_text(), "previous memo")

    def test_invalid_empty_or_truncated_summary_is_failure(self):
        for data in (
            {}, {"choices": []},
            {"choices": [{"message": {"content": None}}]},
            {"choices": [{"message": {"content": " "}}]},
            {"choices": [{"message": {"content": "Partial"}, "finish_reason": "length"}]},
        ):
            with self.subTest(data=data):
                self.reply(data)
                with self.assertRaises(RuntimeError):
                    self.run_memo()
                self.assertEqual(self.output.read_text(), "previous memo")

    def test_missing_key_or_audio_fails_before_api_call(self):
        with patch.dict(os.environ, ASSEMBLYAI_API_KEY=""):
            with self.assertRaisesRegex(RuntimeError, "ASSEMBLYAI_API_KEY"):
                self.run_memo()
        self.audio.unlink()
        with self.assertRaises(FileNotFoundError):
            self.run_memo()
        self.client.assert_not_called()


if __name__ == "__main__":
    unittest.main()
