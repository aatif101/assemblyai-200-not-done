import asyncio
import io
import json
import unittest
import wave
from unittest.mock import patch

from websockets.asyncio.client import connect as real_connect
from websockets.asyncio.server import serve

import transcribe as app


class IntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self, handler):
        buffer = io.BytesIO()
        with wave.open(buffer, 'wb') as wav:
            wav.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
            wav.writeframes(b'\0' * 3430)  # 107 ms: last chunk must be padded.
        buffer.seek(0)
        audit = io.BytesIO()
        async with serve(handler, '127.0.0.1', 0) as server:
            port = server.sockets[0].getsockname()[1]
            with patch.object(app, 'connect', lambda *a, **kw: real_connect(f'ws://127.0.0.1:{port}')):
                with wave.open(buffer, 'rb') as wav:
                    result = await app.transcribe(wav, audit, 'test-key')
        return result, audit.getvalue()

    async def test_pacing_raw_audit_final_replacement_and_revisions(self):
        frames = [json.dumps({'type': 'Begin', 'configuration': {
            'model': app.MODEL, 'domain': 'medical-v1', 'speaker_labels': True, 'redact_pii': True}}),
            '{ "type": "Turn", "turn_order": 2, "speaker_label": "PENDING", "end_of_turn": true, "transcript": "[PERSON_NAME].", "extra": 12 }',
            json.dumps({'type': 'Turn', 'turn_order': 1, 'speaker_label': 'A', 'end_of_turn': True, 'transcript': 'Medication.'}),
            json.dumps({'type': 'Turn', 'turn_order': 1, 'speaker_label': 'A', 'end_of_turn': True, 'transcript': 'Medication confirmed.'}),
            json.dumps({'type': 'SpeakerRevision', 'revisions': [{'turn_order': 2, 'speaker_label': 'A'}]}),
            json.dumps({'type': 'SpeakerRevision', 'revisions': [{'turn_order': 2, 'speaker_label': 'B'}]}),
            '{"type":"Termination"}']
        received_at = []

        async def handler(ws):
            await ws.send(frames[0])
            start = asyncio.get_running_loop().time()
            async for message in ws:
                if isinstance(message, bytes):
                    self.assertEqual(len(message), 1600)
                    received_at.append(asyncio.get_running_loop().time() - start)
                else:
                    self.assertEqual(json.loads(message), {'type': 'Terminate'})
                    for frame in frames[1:]:
                        await ws.send(frame)
                    break

        result, audit = await self.exercise(handler)
        self.assertEqual(result, {'turns': [
            {'speaker': 'A', 'text': 'Medication confirmed.'},
            {'speaker': 'B', 'text': '[PERSON_NAME].'}]})
        self.assertEqual(audit, ('\n'.join(frames) + '\n').encode())
        self.assertEqual(len(received_at), 3)
        self.assertGreaterEqual(received_at[-1], 0.14)

    async def test_incorrect_privacy_configuration_sends_no_audio(self):
        received = []

        async def handler(ws):
            await ws.send(json.dumps({'type': 'Begin', 'configuration': {'redact_pii': False}}))
            async for message in ws:
                received.append(message)

        with self.assertRaises(app.TranscriptionError):
            await self.exercise(handler)
        self.assertEqual(received, [])

    async def test_premature_normal_close_is_failure(self):
        async def handler(ws):
            await ws.send(json.dumps({'type': 'Begin', 'configuration': {
                'model': app.MODEL, 'domain': 'medical-v1', 'speaker_labels': True, 'redact_pii': True}}))
            await ws.close()

        with self.assertRaises(Exception):
            await self.exercise(handler)

    def test_unexpected_partial_rejected(self):
        with self.assertRaises(app.TranscriptionError):
            app.Turns().accept({'type': 'Turn', 'end_of_turn': False})


if __name__ == '__main__':
    unittest.main()
