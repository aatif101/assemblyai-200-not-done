"""Offline checks for the scheduler's output and failure contract."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import assemblyai as aai

import transcribe


def completed_transcript(summary=None):
    return SimpleNamespace(
        id="memo-id",
        status=aai.TranscriptStatus.completed,
        text="Quorvex ships Thursday. Talvanta moves to next sprint.",
        utterances=[
            SimpleNamespace(speaker="A", text="Quorvex ships Thursday."),
            SimpleNamespace(speaker="B", text="Talvanta moves to next sprint."),
        ],
        json_response={
            "speech_understanding": {
                "response": {
                    "summarization": summary or {
                        "status": "success",
                        "block_summary": "Quorvex ships Thursday. Talvanta moves to next sprint.",
                    },
                },
            },
        },
    )


class TranscribeTests(unittest.TestCase):
    def run_main(self, transcript, output):
        with patch.dict("os.environ", {"ASSEMBLYAI_API_KEY": "test-key"}):
            with patch.object(aai.Transcriber, "transcribe", return_value=transcript):
                transcribe.main("memo.wav", str(output))

    def test_output_contract_preserves_speakers(self):
        with TemporaryDirectory() as directory:
            output = Path(directory) / "out.json"
            self.run_main(completed_transcript(), output)
            data = json.loads(output.read_text())
            self.assertEqual(set(data), {"transcript_id", "text", "utterances", "summary"})
            self.assertEqual(data["transcript_id"], "memo-id")
            self.assertEqual(data["utterances"], [
                {"speaker": "A", "text": "Quorvex ships Thursday."},
                {"speaker": "B", "text": "Talvanta moves to next sprint."},
            ])
            self.assertIsInstance(data["summary"], str)
            self.assertTrue(data["summary"])

    def test_transcription_error_keeps_existing_output(self):
        transcript = SimpleNamespace(status=aai.TranscriptStatus.error, error="Bad audio")
        with TemporaryDirectory() as directory:
            output = Path(directory) / "out.json"
            output.write_text("previous result")
            with self.assertRaisesRegex(RuntimeError, "Transcription failed: Bad audio"):
                self.run_main(transcript, output)
            self.assertEqual(output.read_text(), "previous result")

    def test_missing_or_failed_summary_does_not_write_output(self):
        for summary in [
            {"status": "error", "block_summary": "Partial result"},
            {"status": "success", "block_summary": " "},
            {"status": "success"},
        ]:
            with self.subTest(summary=summary), TemporaryDirectory() as directory:
                output = Path(directory) / "out.json"
                with self.assertRaisesRegex(RuntimeError, "Summary failed"):
                    self.run_main(completed_transcript(summary), output)
                self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
