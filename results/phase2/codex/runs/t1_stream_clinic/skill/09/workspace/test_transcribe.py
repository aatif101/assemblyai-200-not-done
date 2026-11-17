import asyncio
import io
import json
import time
import unittest

import transcribe as app


class Socket:
    def __init__(self, frames=()):
        self.frames = iter(frames)
        self.sent = []

    async def recv(self):
        return next(self.frames)

    async def send(self, data):
        self.sent.append((time.monotonic(), data))


class StreamingTests(unittest.IsolatedAsyncioTestCase):
    async def test_audit_and_latest_text_and_revisions(self):
        frames = [
            '{ "type": "Turn", "turn_order": 1, "end_of_turn": true, "transcript": "Call [PHONE_NUMBER].", "speaker_label": "A", "extra": 42 }',
            '{"type":"Turn","turn_order":0,"end_of_turn":true,"transcript":"Hi [PERSON_NAME].","speaker_label":"PENDING"}',
            '{"type":"SpeakerRevision","revisions":[{"turn_order":0,"speaker_label":"A"}]}',
            '{"type":"SpeakerRevision","revisions":[{"turn_order":0,"speaker_label":"B"}]}',
            '{"type":"Termination"}',
        ]
        audit = io.BytesIO()
        turns = {}
        await app.receive_turns(Socket(frames), audit, turns)
        self.assertEqual(audit.getvalue(), ('\n'.join(frames) + '\n').encode())
        self.assertEqual(turns[0], {"speaker": "B", "text": "Hi [PERSON_NAME]."})
        self.assertEqual(turns[1]["text"], "Call [PHONE_NUMBER].")

    async def test_pacing_padding_and_terminate(self):
        class Audio:
            chunks = iter([b'\x01\x02' * 800, b'\x01\x02' * 100, b''])
            def readframes(self, count):
                return next(self.chunks)
        ws = Socket()
        started = time.monotonic()
        await app.send_audio(ws, Audio())
        self.assertGreaterEqual(ws.sent[0][0] - started, 0.049)
        self.assertGreaterEqual(ws.sent[1][0] - ws.sent[0][0], 0.049)
        self.assertEqual(len(ws.sent[1][1]), 1600)
        self.assertEqual(json.loads(ws.sent[-1][1]), {"type": "Terminate"})

    async def test_server_error_is_audited_and_fails(self):
        raw = '{"type":"Error","error":"test"}'
        audit = io.BytesIO()
        with self.assertRaises(app.TranscriptionError):
            await app.read_message(Socket([raw]), audit)
        self.assertEqual(audit.getvalue(), (raw + '\n').encode())

    def test_required_configuration_fails_closed(self):
        config = {"model": app.MODEL, "domain": "medical-v1", "speaker_labels": True, "redact_pii": True}
        app.verify_begin({"type": "Begin", "configuration": config})
        for field in config:
            with self.subTest(field=field):
                with self.assertRaises(app.TranscriptionError):
                    app.verify_begin({"type": "Begin", "configuration": {k: v for k, v in config.items() if k != field}})


if __name__ == '__main__':
    unittest.main()
