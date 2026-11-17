"""Regression checks for the API migration, without network calls."""
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


class TranscriptionTests(unittest.TestCase):
    def test_current_request_and_output_contract(self):
        transcript = SimpleNamespace(
            id="memo-id", status=aai.TranscriptStatus.completed,
            text="Quorvex is ready. Talvanta ships Friday.",
            utterances=[SimpleNamespace(speaker="A", text="Quorvex is ready."),
                        SimpleNamespace(speaker="B", text="Talvanta ships Friday.")],
        )
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "out.json"
            with patch.dict(os.environ, {"ASSEMBLYAI_API_KEY": "test-key"}), \
                 patch.object(transcribe.aai, "Transcriber") as client, \
                 patch.object(transcribe.httpx, "post") as post:
                client.return_value.transcribe.return_value = transcript
                post.return_value = httpx.Response(200, json={"choices": [{
                    "message": {"content": "Quorvex is ready. Talvanta ships Friday."},
                    "finish_reason": "end_turn",
                }]}, request=httpx.Request("POST", "https://example.com"))
                transcribe.main("memo.wav", str(out))
                config = client.return_value.transcribe.call_args.kwargs["config"]
                request = config.raw.dict(exclude_none=True)
                self.assertEqual(request["speech_models"], ["universal-3-5-pro", "universal-2"])
                self.assertTrue(request["speaker_labels"])
                self.assertEqual(request["keyterms_prompt"], transcribe.PRODUCT_NAMES)
                self.assertNotIn("word_boost", request)
                self.assertNotIn("boost_param", request)
                self.assertNotIn("speech_model", request)
                client.assert_called_once_with(api_key="test-key")
                gateway = post.call_args.kwargs
                self.assertEqual(gateway["headers"]["Authorization"], "test-key")
                self.assertIn(transcript.text, gateway["json"]["messages"][1]["content"])
                self.assertIn("exactly two short sentences", gateway["json"]["messages"][1]["content"])
                self.assertEqual(gateway["json"]["temperature"], 0)
                self.assertEqual(json.loads(out.read_text()), {
                    "transcript_id": "memo-id", "text": transcript.text,
                    "utterances": [{"speaker": "A", "text": "Quorvex is ready."},
                                   {"speaker": "B", "text": "Talvanta ships Friday."}],
                    "summary": "Quorvex is ready. Talvanta ships Friday.",
                })

    def test_transcription_failure_preserves_existing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "out.json"
            out.write_text("previous result")
            with patch.dict(os.environ, {"ASSEMBLYAI_API_KEY": "test-key"}), \
                 patch.object(transcribe.aai, "Transcriber") as client, \
                 patch.object(transcribe, "summarize") as summary:
                client.return_value.transcribe.return_value = SimpleNamespace(
                    status=aai.TranscriptStatus.error, error="bad audio")
                with self.assertRaisesRegex(RuntimeError, "bad audio"):
                    transcribe.main("memo.wav", str(out))
                summary.assert_not_called()
                self.assertEqual(out.read_text(), "previous result")

    def test_summary_failures(self):
        for status, body in [
            (401, {"error": "Unauthorized"}),
            (200, {"choices": [{"message": {"content": None}}]}),
            (200, {"choices": [{"message": {"content": ""}}]}),
            (200, {"choices": [{"message": {"content": "Partial"}, "finish_reason": "length"}]}),
        ]:
            with self.subTest(status=status, body=body), \
                 patch.object(transcribe.httpx, "post", return_value=httpx.Response(
                     status, json=body, request=httpx.Request("POST", "https://example.com"))):
                with self.assertRaises((RuntimeError, httpx.HTTPStatusError)):
                    transcribe.summarize("memo text", "test-key")


if __name__ == "__main__":
    unittest.main()
