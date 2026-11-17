"""Regression checks for the API migration; no network or API key required."""
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
    def test_current_api_and_output_contract(self):
        transcript = SimpleNamespace(
            id="test-id", status=aai.TranscriptStatus.completed,
            text="Quorvex and Zyntrix Flow shipped. Talvanta is next.",
            utterances=[SimpleNamespace(speaker="A", text="Quorvex shipped.")],
        )
        response = httpx.Response(200, json={"choices": [{
            "finish_reason": "stop", "message": {"content": "First sentence. Second sentence."}
        }]}, request=httpx.Request("POST", "https://example.com"))
        with TemporaryDirectory() as directory:
            audio = Path(directory) / "audio.wav"
            output = Path(directory) / "out.json"
            audio.write_bytes(b"test audio")
            with patch.dict(os.environ, {"ASSEMBLYAI_API_KEY": "test-key"}), \
                 patch.object(aai, "Transcriber") as client, \
                 patch.object(transcribe.httpx, "post", return_value=response) as post:
                client.return_value.transcribe.return_value = transcript
                transcribe.main(str(audio), str(output))
                config = client.return_value.transcribe.call_args.kwargs["config"]
                self.assertEqual(config.speech_models, ["universal-3-5-pro"])
                self.assertTrue(config.speaker_labels)
                self.assertEqual(config.keyterms_prompt, transcribe.PRODUCT_NAMES)
                self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "test-key")
                self.assertEqual(post.call_args.kwargs["json"]["messages"][1]["content"], transcript.text)
            self.assertEqual(json.loads(output.read_text()), {
                "transcript_id": "test-id", "text": transcript.text,
                "utterances": [{"speaker": "A", "text": "Quorvex shipped."}],
                "summary": "First sentence. Second sentence.",
            })

    def test_transcription_or_summary_failure_preserves_existing_output(self):
        for status in (aai.TranscriptStatus.error, aai.TranscriptStatus.completed):
            with self.subTest(status=status), TemporaryDirectory() as directory:
                audio = Path(directory) / "audio.wav"
                output = Path(directory) / "out.json"
                audio.write_bytes(b"test audio")
                output.write_text("existing output")
                transcript = SimpleNamespace(status=status, error="service failed", text="Quorvex shipped.")
                with patch.dict(os.environ, {"ASSEMBLYAI_API_KEY": "test-key"}), \
                     patch.object(aai, "Transcriber") as client, \
                     patch.object(transcribe, "summarize", side_effect=RuntimeError("summary failed")) as summary:
                    client.return_value.transcribe.return_value = transcript
                    with self.assertRaises(RuntimeError):
                        transcribe.main(str(audio), str(output))
                    if status == aai.TranscriptStatus.error:
                        summary.assert_not_called()
                self.assertEqual(output.read_text(), "existing output")

    def test_invalid_summary_responses_fail(self):
        for body in ({}, {"choices": []}, {"choices": [{"message": {"content": None}}]},
                     {"choices": [{"message": {"content": " "}}]},
                     {"choices": [{"message": {"content": "Partial"}, "finish_reason": "length"}]}):
            with self.subTest(body=body):
                response = httpx.Response(200, json=body, request=httpx.Request("POST", "https://example.com"))
                with patch.object(transcribe.httpx, "post", return_value=response):
                    with self.assertRaises(RuntimeError):
                        transcribe.summarize("Memo", "test-key")


if __name__ == "__main__":
    unittest.main()
