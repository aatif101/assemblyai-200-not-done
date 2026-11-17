import asyncio
import io
import json
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from websockets.asyncio.server import serve
import transcribe as app


class StreamingTests(unittest.IsolatedAsyncioTestCase):
    async def test_roundtrip_pacing_audit_finals_and_revisions(self):
        frames = [
            json.dumps({"type": "Begin", "configuration": {
                "model": app.MODEL, "domain": "medical-v1",
                "speaker_labels": True, "redact_pii": True}}),
            '{ "type": "Turn", "turn_order": 1, "end_of_turn": true, "speaker_label": "A", "transcript": "Metformin.", "extra": 42 }',
            json.dumps({"type": "Turn", "turn_order": 0, "end_of_turn": True,
                        "speaker_label": "PENDING", "transcript": "[PERSON_NAME]."}),
            json.dumps({"type": "SpeakerRevision", "revisions": [
                {"turn_order": 0, "speaker_label": "A"}]}),
            json.dumps({"type": "SpeakerRevision", "revisions": [
                {"turn_order": 0, "speaker_label": "B"}]}),
            json.dumps({"type": "Termination"}),
        ]
        arrivals = []
        async def server(ws):
            params = parse_qs(urlsplit(ws.request.path).query)
            self.assertEqual(params["redact_pii"], ["true"])
            self.assertEqual(params["include_partial_turns"], ["false"])
            self.assertEqual(ws.request.headers["Authorization"], "test-key")
            await ws.send(frames[0])
            async for message in ws:
                if isinstance(message, bytes):
                    self.assertEqual(len(message), 1600)
                    arrivals.append(asyncio.get_running_loop().time())
                    if len(arrivals) == 1:
                        for frame in frames[1:3]:
                            await ws.send(frame)
                else:
                    self.assertEqual(json.loads(message), {"type": "Terminate"})
                    for frame in frames[3:]:
                        await ws.send(frame)
                    return
        async with serve(server, "127.0.0.1", 0) as service:
            port = service.sockets[0].getsockname()[1]
            audit = io.StringIO()
            with patch.object(app, "ENDPOINT", f"ws://127.0.0.1:{port}/v3/ws"):
                result = await app.transcribe(FakeAudio(), audit, "test-key")
        self.assertEqual(audit.getvalue(), "\n".join(frames) + "\n")
        self.assertEqual(result, {"turns": [
            {"speaker": "B", "text": "[PERSON_NAME]."},
            {"speaker": "A", "text": "Metformin."}]})
        self.assertEqual(len(arrivals), 3)
        self.assertGreaterEqual(arrivals[-1] - arrivals[0], 0.095)

    async def test_wrong_configuration_sends_no_audio(self):
        async def server(ws):
            await ws.send(json.dumps({"type": "Begin", "configuration": {}}))
            from websockets.exceptions import ConnectionClosed
            with self.assertRaises(ConnectionClosed):
                await ws.recv()
        async with serve(server, "127.0.0.1", 0) as service:
            port = service.sockets[0].getsockname()[1]
            with patch.object(app, "ENDPOINT", f"ws://127.0.0.1:{port}/v3/ws"):
                with self.assertRaises(app.TranscriptionError):
                    await app.transcribe(FakeAudio(), io.StringIO(), "test-key")

    async def test_close_without_termination_is_failure(self):
        async def server(ws):
            await ws.close()
        async with serve(server, "127.0.0.1", 0) as service:
            port = service.sockets[0].getsockname()[1]
            with patch.object(app, "ENDPOINT", f"ws://127.0.0.1:{port}/v3/ws"):
                with self.assertRaises(Exception):
                    await app.transcribe(FakeAudio(), io.StringIO(), "test-key")


class FakeAudio:
    def __init__(self):
        self.chunks = iter([b"\x01\x00" * 800, b"\x01\x00" * 800, b"\x01\x00" * 7, b""])

    def readframes(self, count):
        return next(self.chunks)


if __name__ == "__main__":
    unittest.main()
