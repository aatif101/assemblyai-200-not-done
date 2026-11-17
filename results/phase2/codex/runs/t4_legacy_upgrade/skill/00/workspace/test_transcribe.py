"""Regression checks for the API migration; no network calls required."""
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import requests

import transcribe


class TranscriptionTests(unittest.TestCase):
    def test_output_contract_and_current_transcription_options(self):
        transcript = SimpleNamespace(
            id="memo-id", status=transcribe.aai.TranscriptStatus.completed,
            text="Quorvex is ready. Talvanta ships next week.",
            utterances=[SimpleNamespace(speaker="A", text="Quorvex is ready."),
                        SimpleNamespace(speaker="B", text="Talvanta ships next week.")],
        )
        with TemporaryDirectory() as directory, \
                patch.dict(os.environ, {"ASSEMBLYAI_API_KEY": "test-key"}), \
                patch.object(transcribe.aai, "Transcriber") as client, \
                patch.object(transcribe, "summarize", return_value="First. Second.") as summary:
            client.return_value.transcribe.return_value = transcript
            output = Path(directory) / "out.json"
            transcribe.main("audio.wav", str(output))
            result = json.loads(output.read_text())
            self.assertEqual(result, {
                "transcript_id": "memo-id", "text": transcript.text,
                "utterances": [{"speaker": u.speaker, "text": u.text}
                               for u in transcript.utterances],
                "summary": "First. Second.",
            })
            config = client.return_value.transcribe.call_args.kwargs["config"]
            self.assertEqual(config.speech_models, ["universal-3-5-pro", "universal-2"])
            self.assertTrue(config.speaker_labels)
            self.assertEqual(config.keyterms_prompt, transcribe.PRODUCT_NAMES)
            summary.assert_called_once_with(transcript.text, "test-key")

    def test_failed_transcription_does_not_summarize_or_overwrite(self):
        transcript = SimpleNamespace(status=transcribe.aai.TranscriptStatus.error,
                                     error="Invalid audio")
        with TemporaryDirectory() as directory, \
                patch.dict(os.environ, {"ASSEMBLYAI_API_KEY": "test-key"}), \
                patch.object(transcribe.aai, "Transcriber") as client, \
                patch.object(transcribe, "summarize") as summary:
            client.return_value.transcribe.return_value = transcript
            output = Path(directory) / "out.json"
            output.write_text("previous output")
            with self.assertRaises(SystemExit):
                transcribe.main("audio.wav", str(output))
            summary.assert_not_called()
            self.assertEqual(output.read_text(), "previous output")

    def test_missing_key_fails_before_api_call(self):
        with patch.dict(os.environ, {}, clear=True), \
                patch.object(transcribe.aai, "Transcriber") as client:
            with self.assertRaisesRegex(RuntimeError, "ASSEMBLYAI_API_KEY"):
                transcribe.main("audio.wav", "out.json")
            client.assert_not_called()


class SummaryTests(unittest.TestCase):
    @patch.object(transcribe.requests, "post")
    def test_gateway_receives_transcript_text_and_raw_key(self, post):
        post.return_value.json.return_value = {
            "choices": [{"message": {"content": " First. Second. "},
                         "finish_reason": "stop"}]
        }
        self.assertEqual(transcribe.summarize("Memo transcript", "test-key"),
                         "First. Second.")
        args, kwargs = post.call_args
        self.assertEqual(args[0], "https://llm-gateway.assemblyai.com/v1/chat/completions")
        self.assertEqual(kwargs["headers"]["Authorization"], "test-key")
        self.assertEqual(kwargs["json"]["messages"][-1]["content"], "Memo transcript")
        self.assertIn("two sentences", kwargs["json"]["messages"][0]["content"])
        self.assertIn("timeout", kwargs)

    @patch.object(transcribe.requests, "post")
    def test_gateway_http_error_is_failure(self, post):
        post.return_value.raise_for_status.side_effect = requests.HTTPError("Unauthorized")
        with self.assertRaises(requests.HTTPError):
            transcribe.summarize("Memo", "test-key")

    @patch.object(transcribe.requests, "post")
    def test_empty_or_truncated_summary_is_failure(self, post):
        for content, reason in [(None, "stop"), ("  ", "stop"), ("Partial", "length")]:
            with self.subTest(content=content, reason=reason):
                post.return_value.json.return_value = {
                    "choices": [{"message": {"content": content}, "finish_reason": reason}]
                }
                with self.assertRaises(RuntimeError):
                    transcribe.summarize("Memo", "test-key")


if __name__ == "__main__":
    unittest.main()
