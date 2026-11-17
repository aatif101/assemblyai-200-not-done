"""Offline regression checks for the API migration and scheduler contract."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx

import transcribe


class TranscriptionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.audio = Path(self.directory.name) / "memo.wav"
        self.audio.write_bytes(b"audio")
        self.output = Path(self.directory.name) / "out.json"
        self.output.write_text("previous result")
        self.transcript = SimpleNamespace(
            id="transcript-123", status=transcribe.aai.TranscriptStatus.completed,
            text="Quorvex is ready. Zyntrix Flow and Talvanta ship Friday.",
            utterances=[SimpleNamespace(speaker="A", text="Quorvex is ready.")],
            error=None,
        )
        self.start_patch(patch.dict("os.environ", {"ASSEMBLYAI_API_KEY": "test-key"}))
        self.transcriber = self.start_patch(patch("transcribe.aai.Transcriber"))
        self.transcriber.return_value.transcribe.return_value = self.transcript
        self.post = self.start_patch(patch("transcribe.httpx.post"))
        self.post.return_value = httpx.Response(200, json={
            "choices": [{"message": {"content": "Quorvex is ready. The rest ships Friday."},
                         "finish_reason": "end_turn"}]
        })

    def start_patch(self, patcher):
        value = patcher.start()
        self.addCleanup(patcher.stop)
        return value

    def run_memo(self):
        transcribe.main(str(self.audio), str(self.output))

    def test_current_api_requests_and_output_contract(self):
        self.run_memo()
        config = self.transcriber.return_value.transcribe.call_args.kwargs["config"]
        payload = config.raw.dict(exclude_none=True)
        self.assertEqual(payload["speech_models"], ["universal-3-5-pro", "universal-2"])
        self.assertTrue(payload["speaker_labels"])
        self.assertEqual(payload["keyterms_prompt"], transcribe.PRODUCT_NAMES)
        self.assertNotIn("word_boost", payload)
        self.assertNotIn("boost_param", payload)
        self.assertNotIn("speech_model", payload)
        self.transcriber.assert_called_once_with(api_key="test-key")
        request = self.post.call_args
        self.assertEqual(request.args[0],
                         "https://llm-gateway.assemblyai.com/v1/chat/completions")
        self.assertEqual(request.kwargs["headers"]["Authorization"], "test-key")
        self.assertIn(self.transcript.text, request.kwargs["json"]["messages"][1]["content"])
        self.assertEqual(request.kwargs["json"]["model"], "qwen3.5-4b-32k-fast")
        self.assertEqual(json.loads(self.output.read_text()), {
            "transcript_id": "transcript-123", "text": self.transcript.text,
            "utterances": [{"speaker": "A", "text": "Quorvex is ready."}],
            "summary": "Quorvex is ready. The rest ships Friday.",
        })

    def test_transcription_failure_skips_summary_and_preserves_output(self):
        self.transcript.status = transcribe.aai.TranscriptStatus.error
        self.transcript.error = "Transcoding failed"
        with self.assertRaisesRegex(RuntimeError, "Transcoding failed"):
            self.run_memo()
        self.post.assert_not_called()
        self.assertEqual(self.output.read_text(), "previous result")

    def test_gateway_failures_preserve_output(self):
        for response in [
            httpx.Response(401, json={"error": "Unauthorized"}),
            httpx.Response(200, json={"choices": []}),
            httpx.Response(200, json={"choices": [{"message": {"content": None}}]}),
            httpx.Response(200, json={"choices": [{"message": {"content": "Partial"},
                                                   "finish_reason": "length"}]}),
        ]:
            with self.subTest(response=response.text):
                self.post.return_value = response
                with self.assertRaisesRegex(RuntimeError, "Summary failed"):
                    self.run_memo()
                self.assertEqual(self.output.read_text(), "previous result")

    def test_missing_key_fails_before_api_call(self):
        with patch.dict("os.environ", {"ASSEMBLYAI_API_KEY": ""}):
            with self.assertRaisesRegex(RuntimeError, "ASSEMBLYAI_API_KEY"):
                self.run_memo()
        self.transcriber.assert_not_called()


if __name__ == "__main__":
    unittest.main()
