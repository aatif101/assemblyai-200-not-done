"""Offline regression checks; run with python -m unittest."""
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

import assemblyai as aai
import transcribe


class TranscribeTests(unittest.TestCase):
    def test_output_and_current_api_options(self):
        transcript = NS(status=aai.TranscriptStatus.completed, id='test-id',
                        text='Quorvex ships Thursday.',
                        utterances=[NS(speaker='A', text='Quorvex ships Thursday.')])
        completion = NS(choices=[NS(finish_reason='stop', message=NS(content='A summary. Second sentence.'))])
        with TemporaryDirectory() as directory, patch.dict(os.environ, ASSEMBLYAI_API_KEY='test'), \
                patch.object(aai, 'Transcriber') as transcriber, \
                patch.object(aai, 'LLMGateway') as gateway:
            transcriber.return_value.transcribe.return_value = transcript
            gateway.return_value.chat.completions.create.return_value = completion
            output = Path(directory) / 'out.json'
            transcribe.main('audio.wav', str(output))
            config = transcriber.return_value.transcribe.call_args.kwargs['config']
            self.assertEqual(config.speech_models, ['universal-3-pro'])
            self.assertTrue(config.speaker_labels)
            self.assertEqual(config.keyterms_prompt, transcribe.PRODUCT_NAMES)
            messages = gateway.return_value.chat.completions.create.call_args.kwargs['messages']
            self.assertEqual(messages[-1]['content'], transcript.text)
            self.assertEqual(json.loads(output.read_text()), {
                'transcript_id': 'test-id', 'text': transcript.text,
                'utterances': [{'speaker': 'A', 'text': transcript.text}],
                'summary': 'A summary. Second sentence.'})

            # A failed summary must not overwrite an existing successful output.
            original = output.read_text()
            for bad in [NS(choices=[]), NS(choices=[NS(finish_reason='length')]),
                        NS(choices=[NS(finish_reason='stop', message=NS(content=''))])]:
                gateway.return_value.chat.completions.create.return_value = bad
                with self.assertRaises(RuntimeError):
                    transcribe.main('audio.wav', str(output))
                self.assertEqual(output.read_text(), original)

    def test_transcription_failure_skips_summary_and_output(self):
        with TemporaryDirectory() as directory, patch.dict(os.environ, ASSEMBLYAI_API_KEY='test'), \
                patch.object(aai, 'Transcriber') as transcriber, \
                patch.object(aai, 'LLMGateway') as gateway:
            transcriber.return_value.transcribe.return_value = NS(
                status=aai.TranscriptStatus.error, error='invalid audio')
            output = Path(directory) / 'out.json'
            with self.assertRaisesRegex(RuntimeError, 'invalid audio'):
                transcribe.main('audio.wav', str(output))
            gateway.assert_not_called()
            self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main()
