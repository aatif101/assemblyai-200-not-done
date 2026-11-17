import io
import json
import unittest
from unittest.mock import patch

import transcribe as app


class TranscriptTests(unittest.TestCase):
    def test_final_replacement_order_and_revision(self):
        turns = app.Turns()
        for order, text in [(2, 'Older'), (1, '[PERSON_NAME]'), (2, 'Final.')]:
            turns.receive({'type': 'Turn', 'turn_order': order,
                           'end_of_turn': True, 'speaker_label': 'A', 'transcript': text})
        turns.receive({'type': 'SpeakerRevision', 'revisions': [
            {'turn_order': 1, 'speaker_label': 'B', 'words': []}]})
        turns.receive({'type': 'SpeakerRevision', 'revisions': [
            {'turn_order': 1, 'speaker_label': 'C', 'words': []}]})
        self.assertEqual(turns.result(), {'turns': [
            {'speaker': 'C', 'text': '[PERSON_NAME]'},
            {'speaker': 'A', 'text': 'Final.'}]})

    def test_reject_missing_redaction_confirmation(self):
        with self.assertRaises(app.SessionError):
            app.check_begin({'configuration': {'model': app.MODEL}})


class SessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_exact_audit_and_termination_revision(self):
        begin = json.dumps({'type': 'Begin', 'configuration': {
            'model': app.MODEL, 'domain': 'medical-v1',
            'speaker_labels': True, 'redact_pii': True}})
        frames = [begin, '{"type":"Turn", "end_of_turn":true,"turn_order":0,"speaker_label":"A","transcript":"[PERSON_NAME]"}',
                  '{"type":"SpeakerRevision","revisions":[{"turn_order":0,"speaker_label":"B"}]}',
                  '{"type":"Termination"}']

        class Socket:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            async def recv(self):
                return frames.pop(0)

        async def send(*args):
            pass

        expected = '\n'.join(frames) + '\n'
        audit = io.StringIO()
        with patch.object(app, 'connect', return_value=Socket()), patch.object(app, 'send_audio', send):
            result = await app.session(None, audit, 'dummy')
        self.assertEqual(audit.getvalue(), expected)
        self.assertEqual(result, {'turns': [{'speaker': 'B', 'text': '[PERSON_NAME]'}]})

    async def test_audio_pacing_padding_and_shutdown(self):
        import time
        class Wav:
            chunks = [b'\1\0' * 800, b'\1\0' * 3, b'']
            def readframes(self, count):
                return self.chunks.pop(0)
        class Socket:
            sent = []
            async def send(self, data):
                self.sent.append((time.monotonic(), data))
        ws = Socket()
        start = time.monotonic()
        await app.send_audio(ws, Wav())
        self.assertGreaterEqual(ws.sent[0][0] - start, 0.049)
        self.assertGreaterEqual(ws.sent[1][0] - ws.sent[0][0], 0.049)
        self.assertEqual(len(ws.sent[1][1]), 1600)
        self.assertEqual([json.loads(data)['type'] for _, data in ws.sent[2:]], ['ForceEndpoint', 'Terminate'])


if __name__ == '__main__':
    unittest.main()
