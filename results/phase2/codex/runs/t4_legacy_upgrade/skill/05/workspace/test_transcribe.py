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


class TranscribeTests(unittest.TestCase):
    def test_api_contract_and_output(self):
        transcript = SimpleNamespace(
            id="test-id", status=aai.TranscriptStatus.completed,
            text="Quorvex ships Thursday.",
            utterances=[SimpleNamespace(speaker="A", text="Quorvex ships Thursday.")],
        )
        response = httpx.Response(200, json={"choices": [{
            "finish_reason": "stop",
            "message": {"content": "Quorvex ships Thursday. Update release notes."},
        }]})
        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / "input.wav"
            audio.write_bytes(b"test audio")
            output = Path(directory) / "output.json"
            with patch.dict(os.environ, ASSEMBLYAI_API_KEY="test-key"), \
                    patch.object(aai, "Transcriber") as client, \
                    patch.object(transcribe.httpx, "post", return_value=response) as post:
                client.return_value.transcribe.return_value = transcript
                transcribe.main(str(audio), str(output))
                client.assert_called_once_with(api_key="test-key")
                config = client.return_value.transcribe.call_args.kwargs["config"]
                self.assertEqual(config.speech_models, ["universal-3-5-pro", "universal-2"])
                self.assertTrue(config.speaker_labels)
                self.assertEqual(config.keyterms_prompt, transcribe.PRODUCT_NAMES)
                self.assertIsNone(config.word_boost)
                self.assertEqual(post.call_args.kwargs["headers"], {"Authorization": "test-key"})
                self.assertEqual(post.call_args.kwargs["json"]["messages"][1]["content"], transcript.text)
            self.assertEqual(json.loads(output.read_text()), {
                "transcript_id": "test-id", "text": transcript.text,
                "utterances": [{"speaker": "A", "text": transcript.text}],
                "summary": "Quorvex ships Thursday. Update release notes.",
            })

    def test_summary_rejects_http_error_empty_and_truncated_results(self):
        responses = [
            httpx.Response(403, json={"error": "denied"}),
            httpx.Response(200, json={"choices": []}),
            httpx.Response(200, json={"choices": [{"message": {"content": None}}]}),
            httpx.Response(200, json={"choices": [{
                "message": {"content": "Partial"}, "finish_reason": "length",
            }]}),
        ]
        for response in responses:
            with self.subTest(response=response.text), \
                    patch.object(transcribe.httpx, "post", return_value=response), \
                    self.assertRaises(RuntimeError):
                transcribe.summarize("Memo", "test-key")

    def test_transcription_failure_preserves_existing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / "input.wav"
            audio.write_bytes(b"invalid audio")
            output = Path(directory) / "output.json"
            output.write_text("existing output")
            with patch.dict(os.environ, ASSEMBLYAI_API_KEY="test-key"), \
                    patch.object(aai, "Transcriber") as client, \
                    patch.object(transcribe, "summarize") as summarize:
                client.return_value.transcribe.return_value = SimpleNamespace(
                    status=aai.TranscriptStatus.error, error="Invalid audio",
                )
                with self.assertRaisesRegex(RuntimeError, "Invalid audio"):
                    transcribe.main(str(audio), str(output))
                summarize.assert_not_called()
            self.assertEqual(output.read_text(), "existing output")


if __name__ == "__main__":
    unittest.main()
