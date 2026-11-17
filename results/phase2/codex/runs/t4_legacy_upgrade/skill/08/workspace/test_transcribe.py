"""Regression checks for the API migration; no paid API calls."""
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import assemblyai as aai
import httpx

import transcribe


class TranscriberTests(unittest.TestCase):
    def response(self, body, status=200):
        return httpx.Response(
            status, json=body,
            request=httpx.Request("POST", "https://llm-gateway.assemblyai.com/v1/chat/completions"),
        )

    def test_current_api_and_output_contract(self):
        text = "Quorvex and Zyntrix Flow shipped. Talvanta is next."
        transcript = SimpleNamespace(
            id="memo-id", status=aai.TranscriptStatus.completed, text=text,
            utterances=[SimpleNamespace(speaker="A", text=text)],
        )
        response = self.response({"choices": [{
            "message": {"content": text}, "finish_reason": "stop",
        }]})
        with tempfile.TemporaryDirectory() as directory, \
                patch.dict(os.environ, {"ASSEMBLYAI_API_KEY": "test-key"}), \
                patch.object(aai, "Transcriber") as client, \
                patch.object(httpx, "post", return_value=response) as gateway:
            client.return_value.transcribe.return_value = transcript
            output = Path(directory) / "out.json"
            transcribe.main("memo.wav", str(output))
            self.assertEqual(json.loads(output.read_text()), {
                "transcript_id": "memo-id", "text": text,
                "utterances": [{"speaker": "A", "text": text}], "summary": text,
            })
            config = client.return_value.transcribe.call_args.kwargs["config"]
            self.assertEqual(config.speech_models, ["universal-3-5-pro"])
            self.assertTrue(config.speaker_labels)
            self.assertEqual(config.keyterms_prompt, transcribe.PRODUCT_NAMES)
            self.assertEqual(gateway.call_args.kwargs["headers"], {"Authorization": "test-key"})
            self.assertEqual(gateway.call_args.kwargs["json"]["messages"][-1]["content"], text)

    def test_failed_transcription_does_not_overwrite_output(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.dict(os.environ, {"ASSEMBLYAI_API_KEY": "test-key"}), \
                patch.object(aai, "Transcriber") as client, \
                patch.object(httpx, "post") as gateway:
            output = Path(directory) / "out.json"
            output.write_text("previous output")
            client.return_value.transcribe.return_value = SimpleNamespace(
                status=aai.TranscriptStatus.error, error="bad audio",
            )
            with self.assertRaisesRegex(RuntimeError, "bad audio"):
                transcribe.main("memo.wav", str(output))
            self.assertEqual(output.read_text(), "previous output")
            gateway.assert_not_called()

    def test_summary_failures_are_errors(self):
        for body, status in [
            ({"message": "access denied"}, 403),
            ({"choices": []}, 200),
            ({"choices": [{"message": {"content": None}}]}, 200),
            ({"choices": [{"message": {"content": "partial"}, "finish_reason": "length"}]}, 200),
        ]:
            with self.subTest(body=body), \
                    patch.object(httpx, "post", return_value=self.response(body, status)):
                with self.assertRaises(RuntimeError):
                    transcribe.summarize("memo", "test-key")

    def test_missing_key(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "ASSEMBLYAI_API_KEY"):
                transcribe.main("memo.wav", "out.json")


if __name__ == "__main__":
    unittest.main()
