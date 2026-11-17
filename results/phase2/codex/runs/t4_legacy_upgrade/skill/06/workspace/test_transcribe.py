"""Contract and failure checks; these tests do not call AssemblyAI."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import assemblyai as aai

import transcribe


class TranscribeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name) / "out.json"
        self.transcript = SimpleNamespace(
            id="memo-id",
            status=aai.TranscriptStatus.completed,
            error=None,
            text="Quorvex ships Thursday. Talvanta moves to next sprint.",
            utterances=[
                SimpleNamespace(speaker="A", text="Quorvex ships Thursday."),
                SimpleNamespace(speaker="B", text="Talvanta moves to next sprint."),
            ],
            json_response={"speech_understanding": {"response": {"summarization": {
                "status": "success",
                "block_summary": "Quorvex ships Thursday. Talvanta moves to next sprint.",
            }}}},
        )
        env = patch.dict(os.environ, {"ASSEMBLYAI_API_KEY": "test-key"})
        env.start()
        self.addCleanup(env.stop)
        mock = patch.object(transcribe.aai, "Transcriber")
        self.client = mock.start()
        self.addCleanup(mock.stop)
        self.client.return_value.transcribe.return_value = self.transcript

    def test_output_contract_and_current_api_request(self):
        transcribe.main("memo.wav", str(self.output))
        self.assertEqual(json.loads(self.output.read_text()), {
            "transcript_id": "memo-id",
            "text": self.transcript.text,
            "utterances": [
                {"speaker": "A", "text": "Quorvex ships Thursday."},
                {"speaker": "B", "text": "Talvanta moves to next sprint."},
            ],
            "summary": self.transcript.text,
        })
        self.client.assert_called_once_with(api_key="test-key")
        call = self.client.return_value.transcribe.call_args
        self.assertEqual(call.args, ("memo.wav",))
        self.assertEqual(call.kwargs["config"].raw.dict(exclude_none=True), {
            "speech_models": ["universal-3-5-pro"],
            "speaker_labels": True,
            "keyterms_prompt": transcribe.PRODUCT_NAMES,
            "speech_understanding": {"request": {"summarization": {
                "summary_type": "paragraph", "effort": "low",
            }}},
        })

    def test_transcription_failure_preserves_existing_output(self):
        self.output.write_text("previous output")
        self.transcript.status = aai.TranscriptStatus.error
        self.transcript.error = "Invalid audio"
        with self.assertRaisesRegex(RuntimeError, "Invalid audio"):
            transcribe.main("memo.wav", str(self.output))
        self.assertEqual(self.output.read_text(), "previous output")

    def test_missing_or_failed_summary_is_failure(self):
        for result in ({}, {"response": None}, {"response": {"summarization": {
            "status": "error", "block_summary": "partial result",
        }}}, {"response": {"summarization": {
            "status": "success", "block_summary": " ",
        }}}):
            with self.subTest(result=result):
                self.transcript.json_response = {"speech_understanding": result}
                with self.assertRaisesRegex(RuntimeError, "Summary failed"):
                    transcribe.main("memo.wav", str(self.output))
                self.assertFalse(self.output.exists())

    def test_no_speech_is_failure(self):
        self.transcript.text = ""
        with self.assertRaisesRegex(RuntimeError, "no speech"):
            transcribe.main("memo.wav", str(self.output))
        self.assertFalse(self.output.exists())

    def test_cli_failures_exit_nonzero(self):
        root = Path(__file__).resolve().parent
        for args, message in (([], "Usage:"), (["sample.wav", str(self.output)],
                                              "ASSEMBLYAI_API_KEY is not set")):
            with self.subTest(args=args):
                env = dict(os.environ)
                env.pop("ASSEMBLYAI_API_KEY", None)
                result = subprocess.run([str(root / "run.sh"), *args], cwd=root,
                                        env=env, capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)
                self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
