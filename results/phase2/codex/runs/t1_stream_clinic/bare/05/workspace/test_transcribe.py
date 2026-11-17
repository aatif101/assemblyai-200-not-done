import asyncio
import io
import json
import unittest
from unittest.mock import patch

import transcribe as app


class FakeSocket:
    def __init__(self, frames=()):
        self.frames = frames
        self.sent = []

    def __aiter__(self):
        return self.iterate()

    async def iterate(self):
        for frame in self.frames:
            yield frame

    async def send(self, data):
        self.sent.append(data)


class Tests(unittest.IsolatedAsyncioTestCase):
    def test_final_turns_replace_partials_and_apply_speaker_revisions(self):
        turns = app.Turns()
        for event in [
            dict(type="Turn", turn_order=0, end_of_turn=False, transcript="partial"),
            dict(type="Turn", turn_order=1, end_of_turn=True, transcript="Later.", speaker_label="B"),
            dict(type="Turn", turn_order=0, end_of_turn=True, transcript="raw", speaker_label="A"),
            dict(type="Turn", turn_order=0, end_of_turn=True, turn_is_formatted=True,
                 transcript="[PERSON_NAME] takes metformin.", speaker_label="A"),
            dict(type="Turn", turn_order=0, end_of_turn=True, transcript="old", speaker_label="A"),
            dict(type="SpeakerRevision", revisions=[dict(turn_order=0, speaker_label="C")]),
        ]:
            turns.accept(event)
        self.assertEqual(turns.result(), {"turns": [
            {"speaker": "C", "text": "[PERSON_NAME] takes metformin."},
            {"speaker": "B", "text": "Later."},
        ]})

    async def test_audit_preserves_exact_wire_messages_and_unknown_fields(self):
        frames = ['{ "type": "Begin", "extra": 123 }',
                  '{"type":"FutureEvent","data":[1,2]}',
                  '{"type":"Termination"}']
        audit = io.StringIO()
        begun, terminated = asyncio.Event(), asyncio.Event()
        await app.receive(FakeSocket(frames), audit, app.Turns(), begun, terminated)
        self.assertEqual(audit.getvalue(), "\n".join(frames) + "\n")
        self.assertTrue(begun.is_set() and terminated.is_set())

    async def test_server_errors_are_audited_and_fail(self):
        raw = '{"type":"Error","error":"test"}'
        audit = io.StringIO()
        with self.assertRaises(app.StreamFailure):
            await app.receive(FakeSocket([raw]), audit, app.Turns(), asyncio.Event(), asyncio.Event())
        self.assertEqual(audit.getvalue(), raw + "\n")

    async def test_disconnect_without_termination_fails(self):
        with self.assertRaises(app.StreamFailure):
            await app.receive(FakeSocket(), io.StringIO(), app.Turns(), asyncio.Event(), asyncio.Event())

    async def test_audio_is_pcm_paced_and_short_tail_is_padded(self):
        class Audio:
            chunks = iter([b"\x01\x02" * 1600, b"\x03\x04" * 10, b""])
            def readframes(self, n):
                self.asserted_size = n
                return next(self.chunks)
        socket = FakeSocket()
        started = asyncio.get_running_loop().time()
        await app.send_audio(socket, Audio())
        elapsed = asyncio.get_running_loop().time() - started
        self.assertGreaterEqual(elapsed, 0.19)
        self.assertEqual(socket.sent[0], b"\x01\x02" * 1600)
        self.assertEqual(socket.sent[1], b"\x03\x04" * 10 + b"\0" * 3180)
        self.assertEqual(json.loads(socket.sent[2]), {"type": "Terminate"})


if __name__ == "__main__":
    unittest.main()
