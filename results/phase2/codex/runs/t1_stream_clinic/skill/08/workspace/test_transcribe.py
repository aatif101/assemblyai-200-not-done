"""Local protocol tests; no API key or external service needed."""
import asyncio
import io
import json
import unittest
import wave
from unittest.mock import patch

from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

import transcribe as app


class StreamingTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self, handler):
        wav = io.BytesIO()
        with wave.open(wav, "wb") as writer:
            writer.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            writer.writeframes(b"\0" * 3500)  # two full chunks and a short tail
        wav.seek(0)
        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            with patch.object(app, "ENDPOINT", f"ws://127.0.0.1:{port}"), \
                    wave.open(wav, "rb") as source:
                audit = io.StringIO()
                result = await app.stream(source, audit, "test-key")
                return result, audit.getvalue()

    async def test_pacing_audit_finals_and_revisions(self):
        frames = []
        received = []

        async def handler(ws):
            self.assertEqual(ws.request.headers["Authorization"], "test-key")
            self.assertIn("include_partial_turns=false", ws.request.path)

            async def send(obj):
                raw = json.dumps(obj, separators=(",", ":"))
                frames.append(raw)
                await ws.send(raw)

            await send({"type": "Begin", "configuration": {
                "model": app.MODEL, "domain": "medical-v1",
                "speaker_labels": True, "redact_pii": True}})
            start = asyncio.get_running_loop().time()
            async for data in ws:
                if isinstance(data, bytes):
                    received.append((asyncio.get_running_loop().time(), data))
                    if len(received) == 1:
                        await send({"type": "Turn", "turn_order": 1,
                                    "end_of_turn": True, "speaker_label": "A",
                                    "transcript": "Hello [PERSON_NAME]."})
                    continue
                self.assertEqual(json.loads(data), {"type": "Terminate"})
                await send({"type": "Turn", "turn_order": 0,
                            "end_of_turn": True, "speaker_label": "B",
                            "transcript": "Metformin."})
                await send({"type": "SpeakerRevision", "revisions": [
                    {"turn_order": 1, "speaker_label": "B"}]})
                await send({"type": "SpeakerRevision", "revisions": [
                    {"turn_order": 1, "speaker_label": "C"}]})
                await send({"type": "Termination"})
                break
            self.assertGreaterEqual(received[-1][0] - start, 0.145)

        result, audit = await self.exercise(handler)
        self.assertEqual(audit, "".join(frame + "\n" for frame in frames))
        self.assertEqual(result, {"turns": [
            {"speaker": "B", "text": "Metformin."},
            {"speaker": "C", "text": "Hello [PERSON_NAME]."}]})
        self.assertEqual([len(data) for _, data in received], [1600] * 3)
        self.assertTrue(all(b[0] - a[0] >= 0.045
                            for a, b in zip(received, received[1:])))

    async def test_reject_unconfirmed_redaction_without_sending_audio(self):
        async def handler(ws):
            await ws.send(json.dumps({"type": "Begin", "configuration": {
                "model": app.MODEL, "domain": "medical-v1",
                "speaker_labels": True, "redact_pii": False}}))
            with self.assertRaises(ConnectionClosed):
                await ws.recv()
        with self.assertRaises(app.TranscriptionError):
            await self.exercise(handler)

    async def test_close_without_termination_fails(self):
        async def handler(ws):
            await ws.send(json.dumps({"type": "Begin", "configuration": {
                "model": app.MODEL, "domain": "medical-v1",
                "speaker_labels": True, "redact_pii": True}}))
            await ws.close()
        with self.assertRaises(Exception):
            await self.exercise(handler)


if __name__ == "__main__":
    unittest.main()
