"""Offline regression checks: python -m unittest -v."""
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx

import transcribe


class TranscribeTests(unittest.TestCase):
    def gateway_response(self, payload, status=200):
        return httpx.Response(
            status, json=payload,
            request=httpx.Request("POST", "https://llm-gateway.assemblyai.com/v1/chat/completions"),
        )

    def test_output_contract_and_current_api_request(self):
        transcript = SimpleNamespace(
            id="test-id", status=transcribe.aai.TranscriptStatus.completed,
            text="Quorvex is ready. Zyntrix Flow and Talvanta are in testing.",
            utterances=[SimpleNamespace(speaker="A", text="Quorvex is ready."),
                        SimpleNamespace(speaker="B", text="Zyntrix Flow and Talvanta are in testing.")],
        )
        summary = "Quorvex is ready. Zyntrix Flow and Talvanta are in testing."
        response = self.gateway_response({"choices": [{
            "message": {"content": summary}, "finish_reason": "stop",
        }]})
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "out.json"
            with patch.dict(os.environ, {"ASSEMBLYAI_API_KEY": "test-key"}), \
                    patch.object(transcribe.aai, "Transcriber") as client, \
                    patch.object(transcribe.httpx, "post", return_value=response) as post:
                client.return_value.transcribe.return_value = transcript
                transcribe.main("input.wav", str(output))
            self.assertEqual(json.loads(output.read_text()), {
                "transcript_id": "test-id", "text": transcript.text,
                "utterances": [{"speaker": u.speaker, "text": u.text} for u in transcript.utterances],
                "summary": summary,
            })
        config = client.return_value.transcribe.call_args.kwargs["config"].raw.dict(exclude_none=True)
        self.assertEqual(config["speech_models"], ["universal-3-5-pro"])
        self.assertEqual(config["keyterms_prompt"], transcribe.PRODUCT_NAMES)
        self.assertTrue(config["speaker_labels"])
        self.assertNotIn("speech_model", config)
        self.assertFalse(config.get("word_boost"))
        self.assertNotIn("boost_param", config)
        self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "test-key")
        messages = post.call_args.kwargs["json"]["messages"]
        self.assertEqual(messages[-1]["content"], transcript.text)

    def test_transcription_failure_does_not_write_or_summarize(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "out.json"
            output.write_text("previous output")
            with patch.dict(os.environ, {"ASSEMBLYAI_API_KEY": "test-key"}), \
                    patch.object(transcribe.aai, "Transcriber") as client, \
                    patch.object(transcribe, "summarize") as summarize:
                client.return_value.transcribe.return_value = SimpleNamespace(
                    status=transcribe.aai.TranscriptStatus.error, error="invalid audio",
                )
                with self.assertRaisesRegex(RuntimeError, "invalid audio"):
                    transcribe.main("bad.wav", str(output))
            summarize.assert_not_called()
            self.assertEqual(output.read_text(), "previous output")

    def test_gateway_http_failure(self):
        with patch.object(transcribe.httpx, "post", return_value=self.gateway_response(
                {"error": "unavailable"}, 503)):
            with self.assertRaises(httpx.HTTPStatusError):
                transcribe.summarize("memo", "test-key")

    def test_gateway_invalid_or_incomplete_summary(self):
        for payload in ({}, {"choices": []}, {"choices": [{"message": {"content": None}}]},
                        {"choices": [{"message": {"content": " "}}]},
                        {"choices": [{"message": {"content": "partial"}, "finish_reason": "length"}]}):
            with self.subTest(payload=payload), patch.object(
                    transcribe.httpx, "post", return_value=self.gateway_response(payload)):
                with self.assertRaisesRegex(RuntimeError, "Summary failed"):
                    transcribe.summarize("memo", "test-key")

    def test_missing_key(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "ASSEMBLYAI_API_KEY"):
                transcribe.main("input.wav", "unused.json")


if __name__ == "__main__":
    unittest.main()
