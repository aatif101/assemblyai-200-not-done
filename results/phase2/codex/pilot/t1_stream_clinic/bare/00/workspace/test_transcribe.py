import asyncio
import io
import json
import time
import unittest

from transcribe import Transcript, receive, send_audio


class Messages:
    def __init__(self, frames):
        self.frames = frames

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.frames:
            raise StopAsyncIteration
        return self.frames.pop(0)


class StreamingTests(unittest.IsolatedAsyncioTestCase):
    async def test_audit_preserves_payloads_and_final_updates(self):
        frames = [
            '{ "type": "Begin", "configuration": {"redact_pii": true, "speaker_labels": true, "domain": "medical-v1"} }',
            '{"type":"Turn","turn_order":1,"end_of_turn":false,"transcript":"partial"}',
            '{"type":"Turn","turn_order":1,"end_of_turn":true,"transcript":"metformin","speaker_label":"B"}',
            '{"type":"Turn","turn_order":0,"end_of_turn":true,"transcript":"hello","speaker_label":"A"}',
            '{"type":"Turn","turn_order":1,"end_of_turn":true,"turn_is_formatted":true,"transcript":"Metformin.","speaker_label":"B"}',
            '{"type":"SpeakerRevision","revisions":[{"turn_order":0,"speaker_label":"C"}]}',
            '{"type":"Termination"}',
        ]
        expected = ("\n".join(frames) + "\n").encode()
        transcript, audit = Transcript(), io.BytesIO()
        begun, terminated = asyncio.Event(), asyncio.Event()
        await receive(Messages(frames), audit, transcript, begun, terminated)
        self.assertEqual(audit.getvalue(), expected)
        self.assertTrue(begun.is_set() and terminated.is_set())
        self.assertEqual(transcript.result(), {"turns": [
            {"speaker": "C", "text": "hello"},
            {"speaker": "B", "text": "Metformin."},
        ]})

    async def test_server_failure_is_logged_and_fails(self):
        for frames in ([ '{"type":"Error","error":"failed"}' ], []):
            with self.subTest(frames=frames):
                expected = "".join(f + "\n" for f in frames).encode()
                audit = io.BytesIO()
                with self.assertRaises(RuntimeError):
                    await receive(Messages(frames), audit, Transcript(), asyncio.Event(), asyncio.Event())
                self.assertEqual(audit.getvalue(), expected)

    async def test_audio_is_paced_and_termination_follows_all_samples(self):
        class Audio:
            def __init__(self):
                self.data = io.BytesIO(b"\x01\x00" * 3700)

            def readframes(self, count):
                return self.data.read(count * 2)

        class Socket:
            def __init__(self):
                self.messages = []

            async def send(self, data):
                self.messages.append((time.monotonic(), data))

        ws = Socket()
        start = time.monotonic()
        await send_audio(ws, Audio())
        binary = [(when, data) for when, data in ws.messages if isinstance(data, bytes)]
        samples = 0
        for when, data in binary:
            samples += len(data) // 2
            self.assertGreaterEqual(when - start, samples / 16000 - .015)
        self.assertEqual(b"".join(data for _, data in binary)[:7400], b"\x01\x00" * 3700)
        self.assertEqual(json.loads(ws.messages[-1][1]), {"type": "Terminate"})


if __name__ == "__main__":
    unittest.main()
