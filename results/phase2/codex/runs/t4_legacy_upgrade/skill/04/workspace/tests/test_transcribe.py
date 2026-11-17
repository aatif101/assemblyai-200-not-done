import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

import assemblyai as aai
import transcribe


class TranscriptionTests(unittest.TestCase):
    def test_success_preserves_schema_and_current_api_parameters(self):
        transcript = SimpleNamespace(
            id="test-id", status=aai.TranscriptStatus.completed,
            text="Quorvex ships Thursday. Talvanta moves to next sprint.",
            utterances=[SimpleNamespace(speaker="A", text="Quorvex ships Thursday.")],
        )
        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / "audio.wav"
            audio.write_bytes(b"audio")
            output = Path(directory) / "out.json"
            with patch.dict(os.environ, {"ASSEMBLYAI_API_KEY": "test-key"}), \
                    patch.object(aai, "Transcriber") as client, \
                    patch.object(transcribe, "summarize", return_value="First sentence. Second sentence.") as summary:
                client.return_value.transcribe.return_value = transcript
                transcribe.main(str(audio), str(output))
            result = json.loads(output.read_text())
        self.assertEqual(set(result), {"transcript_id", "text", "utterances", "summary"})
        self.assertEqual(result["utterances"], [{"speaker": "A", "text": "Quorvex ships Thursday."}])
        summary.assert_called_once_with(transcript.text, "test-key")
        client.assert_called_once_with(api_key="test-key")
        payload = client.return_value.transcribe.call_args.kwargs["config"].raw.dict(exclude_none=True)
        self.assertEqual(payload, {
            "speech_models": ["universal-3-5-pro", "universal-2"],
            "speaker_labels": True,
            "keyterms_prompt": ["Quorvex", "Zyntrix Flow", "Talvanta"],
        })

    def test_gateway_sends_text_and_raw_key(self):
        response = io.BytesIO(json.dumps({"choices": [{
            "message": {"content": " First sentence. Second sentence. "},
            "finish_reason": "stop",
        }]}).encode())
        with patch.object(transcribe.urllib.request, "urlopen", return_value=response) as send:
            self.assertEqual(transcribe.summarize("memo text", "test-key"), "First sentence. Second sentence.")
        request = send.call_args.args[0]
        self.assertEqual(request.full_url, "https://llm-gateway.assemblyai.com/v1/chat/completions")
        self.assertEqual(request.get_header("Authorization"), "test-key")
        payload = json.loads(request.data)
        self.assertEqual(payload["messages"][-1], {"role": "user", "content": "memo text"})
        self.assertNotIn("transcript_ids", payload)

    def test_gateway_rejects_empty_malformed_and_truncated_responses(self):
        for result in [
            {}, {"choices": []},
            {"choices": [{"message": {"content": None}}]},
            {"choices": [{"message": {"content": " "}}]},
            {"choices": [{"message": {"content": "Incomplete"}, "finish_reason": "length"}]},
        ]:
            with self.subTest(result=result):
                with patch.object(transcribe.urllib.request, "urlopen", return_value=io.BytesIO(json.dumps(result).encode())):
                    with self.assertRaises(RuntimeError):
                        transcribe.summarize("memo", "test-key")

    def test_gateway_http_failure_is_reported_without_key(self):
        error = HTTPError("https://example.com", 400, "Bad request", {}, io.BytesIO(b"test-key access denied"))
        with patch.object(transcribe.urllib.request, "urlopen", side_effect=error):
            with self.assertRaisesRegex(RuntimeError, r"HTTP 400.*\[REDACTED\] access denied"):
                transcribe.summarize("memo", "test-key")

    def test_failures_preserve_existing_output(self):
        for status, text, summary_error in [
            (aai.TranscriptStatus.error, None, False),
            (aai.TranscriptStatus.completed, "", False),
            (aai.TranscriptStatus.completed, "Memo text", True),
        ]:
            with self.subTest(status=status, text=text), tempfile.TemporaryDirectory() as directory:
                audio = Path(directory) / "audio.wav"
                audio.write_bytes(b"audio")
                output = Path(directory) / "out.json"
                output.write_text("existing output")
                transcript = SimpleNamespace(status=status, text=text, error="test failure")
                with patch.dict(os.environ, {"ASSEMBLYAI_API_KEY": "test-key"}), \
                        patch.object(aai, "Transcriber") as client, \
                        patch.object(transcribe, "summarize", side_effect=RuntimeError("gateway failed")) as summary:
                    client.return_value.transcribe.return_value = transcript
                    with self.assertRaises(RuntimeError):
                        transcribe.main(str(audio), str(output))
                self.assertEqual(output.read_text(), "existing output")
                self.assertEqual(summary.called, summary_error)


if __name__ == "__main__":
    unittest.main()
