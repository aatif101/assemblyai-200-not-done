import asyncio
import io
import json
import time
import unittest
from unittest.mock import patch

from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

import transcribe as app


BEGIN = {"type": "Begin", "configuration": {
    "model": "universal-3-6-pro", "domain": "medical-v1",
    "speaker_labels": True, "redact_pii": True,
}}


class Audio:
    def __init__(self):
        self.chunks = iter([b"\1\0" * 800, b"\2\0" * 5, b""])

    def readframes(self, count):
        return next(self.chunks)


class StreamingTests(unittest.IsolatedAsyncioTestCase):
    async def run_session(self, handler, audio=None):
        audit = io.BytesIO()
        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            def local_connect(url, **kwargs):
                self.assertIn("include_partial_turns=false", url)
                self.assertIn("redact_pii=true", url)
                self.assertEqual(kwargs["additional_headers"]["Authorization"], "test-key")
                return connect(f"ws://127.0.0.1:{port}", **kwargs)
            with patch.object(app, "connect", local_connect):
                result = await app.session(audio or Audio(), audit, "test-key")
        return result, audit.getvalue()

    async def test_pacing_padding_audit_and_revisions(self):
        raw_messages = [json.dumps(BEGIN),
            '{ "type": "Turn", "turn_order": 2, "end_of_turn": true, "transcript": "Call [PHONE_NUMBER].", "speaker_label": "A" }',
            '{"type":"Turn","turn_order":1,"end_of_turn":true,"transcript":"I am [PERSON_NAME].","speaker_label":"PENDING"}',
            '{"type":"SpeakerRevision","revisions":[{"turn_order":1,"speaker_label":"B"}]}',
            '{"type":"SpeakerRevision","revisions":[{"turn_order":1,"speaker_label":"A"}]}',
            '{"type":"Termination"}']
        packets, times = [], []
        async def handler(ws):
            await ws.send(raw_messages[0])
            started = time.monotonic()
            async for message in ws:
                if isinstance(message, bytes):
                    packets.append(message)
                    times.append(time.monotonic() - started)
                    if len(packets) == 1:
                        # Receive finals while upload is still ongoing.
                        for raw in raw_messages[1:3]:
                            await ws.send(raw)
                else:
                    self.assertEqual(json.loads(message), {"type": "Terminate"})
                    self.assertGreaterEqual(time.monotonic() - started, 0.09)
                    for raw in raw_messages[3:]:
                        await ws.send(raw)
                    break
        result, audit = await self.run_session(handler)
        self.assertEqual(audit, ("\n".join(raw_messages) + "\n").encode())
        self.assertGreaterEqual(times[1] - times[0], 0.04)
        self.assertEqual([len(p) for p in packets], [1600, 1600])
        self.assertEqual(packets[1], b"\2\0" * 5 + bytes(1590))
        self.assertEqual(result, {"turns": [
            {"speaker": "A", "text": "I am [PERSON_NAME]."},
            {"speaker": "A", "text": "Call [PHONE_NUMBER]."}]})

    async def test_configuration_mismatch_sends_no_audio(self):
        received = []
        async def handler(ws):
            bad = {"type": "Begin", "configuration": dict(BEGIN["configuration"], redact_pii=False)}
            await ws.send(json.dumps(bad))
            async for message in ws:
                received.append(message)
        with self.assertRaisesRegex(app.TranscriptionError, "no audio sent"):
            await self.run_session(handler)
        self.assertEqual(received, [])

    async def test_premature_normal_close_fails(self):
        async def handler(ws):
            await ws.send(json.dumps(BEGIN))
            await ws.recv()
            await ws.close()
        with self.assertRaisesRegex(app.TranscriptionError, "before Termination"):
            await self.run_session(handler)

    async def test_server_error_fails(self):
        async def handler(ws):
            await ws.send('{"type":"Error","error":"test failure"}')
        with self.assertRaisesRegex(app.TranscriptionError, "AssemblyAI reported"):
            await self.run_session(handler)


if __name__ == "__main__":
    unittest.main()
