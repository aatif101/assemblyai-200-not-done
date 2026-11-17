"""Offline failure checks: python -m unittest -v."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx

import transcribe


class FailureTests(unittest.TestCase):
    def response(self, status, body):
        return httpx.Response(
            status, json=body,
            request=httpx.Request("POST", "https://llm-gateway.assemblyai.com/v1/chat/completions"),
        )

    def test_invalid_summaries_fail(self):
        for content, reason in [(None, "stop"), ("", "stop"), ("  ", "stop"),
                                ("Partial summary", "length"), ("Partial", "max_tokens")]:
            with self.subTest(content=content, reason=reason):
                response = self.response(200, {"choices": [{
                    "finish_reason": reason, "message": {"content": content},
                }]})
                with patch("transcribe.httpx.post", return_value=response):
                    with self.assertRaises(RuntimeError):
                        transcribe.summarize("Memo text", "test-key")

    @patch.dict("os.environ", {"ASSEMBLYAI_API_KEY": "test-key"})
    def test_api_failures_preserve_existing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / "memo.wav"
            audio.write_bytes(b"test audio")
            output = Path(directory) / "out.json"
            output.write_text("previous successful output")
            transcript = SimpleNamespace(
                status=transcribe.aai.TranscriptStatus.completed,
                text="Quorvex ships Thursday.",
            )
            for status in (400, 401, 429, 500):
                with self.subTest(status=status):
                    response = self.response(status, {"message": "API request failed"})
                    with patch("transcribe.aai.Transcriber") as client:
                        client.return_value.transcribe.return_value = transcript
                        with patch("transcribe.httpx.post", return_value=response):
                            with self.assertRaisesRegex(RuntimeError, str(status)):
                                transcribe.main(str(audio), str(output))
                    self.assertEqual(output.read_text(), "previous successful output")

    @patch.dict("os.environ", {"ASSEMBLYAI_API_KEY": "test-key"})
    def test_transcription_error_skips_summary_and_output(self):
        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / "memo.wav"
            audio.touch()
            output = Path(directory) / "out.json"
            transcript = SimpleNamespace(
                status=transcribe.aai.TranscriptStatus.error, error="Transcoding failed",
            )
            with patch("transcribe.aai.Transcriber") as client:
                client.return_value.transcribe.return_value = transcript
                with patch("transcribe.httpx.post") as gateway:
                    with self.assertRaisesRegex(RuntimeError, "Transcoding failed"):
                        transcribe.main(str(audio), str(output))
                    gateway.assert_not_called()
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
