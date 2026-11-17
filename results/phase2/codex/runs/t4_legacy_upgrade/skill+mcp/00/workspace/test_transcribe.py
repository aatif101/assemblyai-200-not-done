"""Offline migration and failure regression checks: python3 -m unittest -v."""
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import assemblyai as aai
import httpx

import transcribe


class TranscribeTests(unittest.TestCase):
    def response(self, content="First sentence. Second sentence.", reason="stop"):
        return httpx.Response(
            200,
            request=httpx.Request("POST", "https://example.com"),
            json={"choices": [{"message": {"content": content}, "finish_reason": reason}]},
        )

    def test_pipeline_preserves_schema_and_serializes_current_options(self):
        transcript = SimpleNamespace(
            id="memo-id", status=aai.TranscriptStatus.completed,
            text="Quorvex ships Thursday.",
            utterances=[SimpleNamespace(speaker="A", text="Quorvex ships Thursday.")],
        )
        with TemporaryDirectory() as directory, patch.dict(
            os.environ, {"ASSEMBLYAI_API_KEY": "test-key"}
        ), patch.object(transcribe.aai, "Transcriber") as factory, patch.object(
            transcribe.httpx, "post", return_value=self.response()
        ) as post:
            factory.return_value.transcribe.return_value = transcript
            output = Path(directory) / "out.json"
            transcribe.main("memo.wav", str(output))
            self.assertEqual(json.loads(output.read_text()), {
                "transcript_id": "memo-id", "text": transcript.text,
                "utterances": [{"speaker": "A", "text": transcript.text}],
                "summary": "First sentence. Second sentence.",
            })
            config = factory.return_value.transcribe.call_args.kwargs["config"]
            payload = config.raw.dict(exclude_none=True)
            self.assertEqual(payload["speech_models"], ["universal-3-5-pro", "universal-2"])
            self.assertTrue(payload["speaker_labels"])
            self.assertEqual(payload["keyterms_prompt"], transcribe.PRODUCT_NAMES)
            self.assertNotIn("word_boost", payload)
            self.assertNotIn("boost_param", payload)
            self.assertNotIn("speech_model", payload)
            request = post.call_args.kwargs
            self.assertEqual(request["headers"]["Authorization"], "test-key")
            self.assertEqual(request["json"]["messages"][-1]["content"], transcript.text)

    def test_failed_transcription_never_summarizes_or_overwrites_output(self):
        with TemporaryDirectory() as directory, patch.dict(
            os.environ, {"ASSEMBLYAI_API_KEY": "test-key"}
        ), patch.object(transcribe.aai, "Transcriber") as factory, patch.object(
            transcribe, "summarize"
        ) as summarize:
            output = Path(directory) / "out.json"
            output.write_text("previous result")
            factory.return_value.transcribe.return_value = SimpleNamespace(
                status=aai.TranscriptStatus.error, error="invalid audio"
            )
            with self.assertRaisesRegex(RuntimeError, "invalid audio"):
                transcribe.main("bad.wav", str(output))
            summarize.assert_not_called()
            self.assertEqual(output.read_text(), "previous result")

    def test_missing_key_fails(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "ASSEMBLYAI_API_KEY"):
                transcribe.main("memo.wav", "out.json")

    def test_summary_http_error_fails(self):
        response = httpx.Response(
            401, request=httpx.Request("POST", "https://example.com"),
            json={"error": "unauthorized"},
        )
        with patch.object(transcribe.httpx, "post", return_value=response):
            with self.assertRaises(httpx.HTTPStatusError):
                transcribe.summarize("memo", "test-key")

    def test_empty_or_truncated_summary_fails(self):
        for content, reason in [(None, "stop"), ("  ", "stop"), ("Partial", "length")]:
            with self.subTest(content=content, reason=reason), patch.object(
                transcribe.httpx, "post", return_value=self.response(content, reason)
            ):
                with self.assertRaisesRegex(RuntimeError, "Summary failed"):
                    transcribe.summarize("memo", "test-key")


if __name__ == "__main__":
    unittest.main()
