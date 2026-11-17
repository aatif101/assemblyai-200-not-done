import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import transcribe


class TranscriptionTests(unittest.TestCase):
    def completed(self):
        return {
            'status': 'completed', 'redact_pii': True,
            'redact_pii_policies': transcribe.POLICIES,
            'text': '[PERSON_NAME]: [PHONE_NUMBER]',
            'utterances': [{'speaker': 'B', 'text': '[PHONE_NUMBER]', 'start': 100}],
        }

    def test_api_workflow_and_privacy_before_submission(self):
        calls = []

        def api(key, method, path, **kwargs):
            calls.append((method, path))
            if path == '/upload':
                self.assertEqual(kwargs['audio'].read(), b'audio')
                return {'upload_url': 'https://example.com/audio'}
            if method == 'POST':
                config = kwargs['payload']
                self.assertTrue(config['redact_pii'])
                self.assertFalse(config['redact_pii_return_unredacted'])
                self.assertEqual(set(config['redact_pii_policies']),
                                 {'person_name', 'us_social_security_number', 'phone_number'})
                self.assertTrue(config['speaker_labels'])
                self.assertEqual(config['speaker_options']['max_speakers_expected'], 2)
                return {'id': 'test-id'}
            return self.completed()

        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / 'audio.wav'
            audio.write_bytes(b'audio')
            with patch.object(transcribe, 'api_request', side_effect=api), contextlib.redirect_stderr(io.StringIO()):
                result = transcribe.transcribe('test-key', audio)
        self.assertEqual(result, {'transcript_id': 'test-id',
                                 'text': '[PERSON_NAME]: [PHONE_NUMBER]',
                                 'utterances': [{'speaker': 'B', 'text': '[PHONE_NUMBER]'}]})
        self.assertEqual(calls, [('POST', '/upload'), ('POST', '/transcript'),
                                 ('GET', '/transcript/test-id')])

    def test_rejects_missing_redaction_or_unredacted_data(self):
        for changes in ({'redact_pii': False}, {'redact_pii_policies': ['person_name']},
                        {'redact_pii_return_unredacted': True}, {'unredacted_text': 'sensitive'}):
            with self.subTest(changes=changes), self.assertRaises(transcribe.TranscriptionError):
                transcribe.project_transcript(self.completed() | changes, 'test-id')

    def test_service_failure_does_not_expose_error_text(self):
        replies = [{'upload_url': 'https://example.com/audio'}, {'id': 'test-id'},
                   {'status': 'error', 'error': 'SENSITIVE ERROR BODY'}]
        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / 'audio.wav'
            audio.write_bytes(b'audio')
            with patch.object(transcribe, 'api_request', side_effect=replies), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(transcribe.TranscriptionError) as raised:
                    transcribe.transcribe('test-key', audio)
        self.assertNotIn('SENSITIVE', str(raised.exception))

    def test_atomic_write_failure_preserves_previous_output(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'out.json'
            output.write_text('{"previous": true}')
            with patch.object(transcribe.os, 'replace', side_effect=OSError):
                with self.assertRaises(OSError):
                    transcribe.write_output(output, {'text': '[PERSON_NAME]'})
            self.assertEqual(json.loads(output.read_text()), {'previous': True})
            self.assertEqual(list(Path(directory).iterdir()), [output])

    def test_cli_failure_exit_codes(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'out.json'
            env = os.environ.copy()
            env.pop('ASSEMBLYAI_API_KEY', None)
            result = subprocess.run(['./run.sh', 'sample.wav', str(output)], env=env,
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(output.exists())
            result = subprocess.run(['./run.sh'], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)


if __name__ == '__main__':
    unittest.main()
