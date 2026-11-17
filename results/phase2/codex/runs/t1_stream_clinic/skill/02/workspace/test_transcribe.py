import asyncio
import io
import json
import unittest
from unittest.mock import patch

import transcribe as app


class Socket:
    def __init__(self, frames):
        self.frames = iter(frames)

    async def recv(self):
        return next(self.frames)


class TranscriptTests(unittest.TestCase):
    def test_final_replacement_order_and_revision_deltas(self):
        turns = app.Turns()
        for message in [
            {"type": "Turn", "turn_order": 2, "end_of_turn": True, "speaker_label": "PENDING", "transcript": "Call [PHONE_NUMBER]."},
            {"type": "Turn", "turn_order": 1, "end_of_turn": True, "speaker_label": "A", "transcript": "Hello"},
            {"type": "Turn", "turn_order": 1, "end_of_turn": True, "speaker_label": "A", "transcript": "Hello."},
            {"type": "SpeakerRevision", "revisions": [{"turn_order": 1, "speaker_label": "B"}, {"turn_order": 2, "speaker_label": "A"}]},
            {"type": "SpeakerRevision", "revisions": [{"turn_order": 1, "speaker_label": "C"}]},
        ]:
            turns.accept(message)
        self.assertEqual(turns.result(), {"turns": [
            {"speaker": "C", "text": "Hello."},
            {"speaker": "A", "text": "Call [PHONE_NUMBER]."},
        ]})

    def test_configuration_is_required_before_audio(self):
        config = {"model": app.MODEL, "domain": "medical-v1", "speaker_labels": True, "redact_pii": True}
        app.check_begin({"type": "Begin", "configuration": config})
        for key in config:
            with self.subTest(key=key), self.assertRaises(app.TranscriptionError):
                app.check_begin({"type": "Begin", "configuration": {k: v for k, v in config.items() if k != key}})

    def test_audit_preserves_payload_and_order_even_errors(self):
        frames = ['{ "type": "Heartbeat", "future_field": [1, 2] }', '{"type":"Error","error":"test"}']
        audit = io.BytesIO()

        async def run():
            socket = Socket(frames)
            self.assertEqual((await app.receive(socket, audit))["future_field"], [1, 2])
            with self.assertRaises(app.TranscriptionError):
                await app.receive(socket, audit)

        asyncio.run(run())
        self.assertEqual(audit.getvalue(), ("\n".join(frames) + "\n").encode())

    def test_partial_is_a_protocol_failure(self):
        with self.assertRaises(app.TranscriptionError):
            app.Turns().accept({"type": "Turn", "end_of_turn": False, "transcript": "draft"})

    def test_pcm_pacing_tail_padding_and_termination(self):
        class Audio:
            chunks = iter([b"a" * 1600, b"b" * 2, b""])

            def readframes(self, count):
                self.assert_count = count
                return next(self.chunks)

        class Output:
            def __init__(self):
                self.messages = []

            async def send(self, message):
                self.messages.append(message)

        sleeps = []

        async def sleep(delay):
            sleeps.append(delay)

        output = Output()
        with patch.object(app.asyncio, "sleep", sleep):
            asyncio.run(app.send_audio(output, Audio()))
        self.assertEqual(output.messages[0], b"a" * 1600)
        self.assertEqual(output.messages[1], b"b" * 2 + b"\0" * 1598)
        self.assertEqual(len(output.messages), 63)
        self.assertEqual(json.loads(output.messages[-1]), {"type": "Terminate"})
        self.assertEqual(len(sleeps), 62)
        self.assertTrue(all(delay > 0 for delay in sleeps))


if __name__ == "__main__":
    unittest.main()
