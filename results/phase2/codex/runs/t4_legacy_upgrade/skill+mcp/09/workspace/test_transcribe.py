"""Regression checks for the API migration and output contract (no API calls)."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

import assemblyai as aai
import transcribe


class TranscribeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.audio = Path(self.temp.name) / 'memo.wav'
        self.audio.write_bytes(b'audio')
        self.output = Path(self.temp.name) / 'out.json'
        self.transcript = NS(
            id='transcript-id', status=aai.TranscriptStatus.completed,
            text='Quorvex ships Friday. Talvanta is delayed.',
            utterances=[NS(speaker='A', text='Quorvex ships Friday.'),
                        NS(speaker='B', text='Talvanta is delayed.')],
        )

    def run_memo(self, transcript=None, summary='Quorvex ships Friday. Talvanta is delayed.', finish='stop'):
        with patch.dict('os.environ', {'ASSEMBLYAI_API_KEY': 'test-key'}), \
             patch('transcribe.aai.Transcriber') as stt, \
             patch('transcribe.aai.LLMGateway') as gateway:
            stt.return_value.transcribe.return_value = transcript or self.transcript
            gateway.return_value.chat.completions.create.return_value = NS(
                choices=[NS(message=NS(content=summary), finish_reason=finish)])
            transcribe.main(str(self.audio), str(self.output))
            return stt, gateway

    def test_output_and_current_api_configuration(self):
        stt, gateway = self.run_memo()
        config = stt.return_value.transcribe.call_args.kwargs['config']
        self.assertEqual(config.speech_models, ['universal-3-5-pro'])
        self.assertTrue(config.speaker_labels)
        self.assertEqual(config.keyterms_prompt, transcribe.PRODUCT_NAMES)
        self.assertFalse(config.word_boost)
        request = gateway.return_value.chat.completions.create.call_args.kwargs
        self.assertIn(self.transcript.text, request['messages'][1]['content'])
        self.assertNotIn('transcript_ids', request)
        self.assertEqual(json.loads(self.output.read_text()), {
            'transcript_id': 'transcript-id', 'text': self.transcript.text,
            'utterances': [{'speaker': 'A', 'text': 'Quorvex ships Friday.'},
                           {'speaker': 'B', 'text': 'Talvanta is delayed.'}],
            'summary': self.transcript.text,
        })

    def test_failed_transcription_does_not_write_output(self):
        with self.assertRaises(SystemExit) as error:
            self.run_memo(transcript=NS(status=aai.TranscriptStatus.error, error='bad audio'))
        self.assertIn('bad audio', str(error.exception))
        self.assertFalse(self.output.exists())

    def test_failed_summary_does_not_overwrite_existing_output(self):
        self.output.write_text('previous result')
        for summary, finish in [(None, 'stop'), ('', 'stop'), ('partial', 'length')]:
            with self.subTest(summary=summary, finish=finish), self.assertRaises(SystemExit):
                self.run_memo(summary=summary, finish=finish)
            self.assertEqual(self.output.read_text(), 'previous result')


if __name__ == '__main__':
    unittest.main()
