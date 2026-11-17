"""Protocol tests with a local WebSocket server; no API key or patient data."""
import asyncio
import io
import json
import time
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
import wave

from websockets.asyncio.server import serve

import transcribe as app


BEGIN = {"type": "Begin", "configuration": {
    "model": app.MODEL, "domain": "medical-v1",
    "speaker_labels": True, "redact_pii": True,
}}


def audio_file():
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(16000)
        writer.writeframes(b"\x00\x00" * 2000)  # 125 ms; short final chunk.
    buffer.seek(0)
    return wave.open(buffer, "rb")


class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def run_session(self, handler):
        audit = io.BytesIO()
        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            with patch.object(app, "ENDPOINT", f"ws://127.0.0.1:{port}/v3/ws"):
                with audio_file() as audio:
                    result = await app.stream(audio, audit, "test-key")
        return result, audit.getvalue()

    async def test_pacing_frames_final_replacement_and_revisions(self):
        sent = []
        chunks = []
        timing = []

        async def handler(ws):
            query = parse_qs(urlsplit(ws.request.path).query)
            self.assertEqual(query["include_partial_turns"], ["false"])
            self.assertEqual(query["redact_pii"], ["true"])
            self.assertIn("person_name", json.loads(query["redact_pii_policies"][0]))
            self.assertEqual(ws.request.headers["Authorization"], "test-key")

            async def emit(message):
                raw = json.dumps(message, separators=(",", ":"))
                sent.append(raw.encode() + b"\n")
                await ws.send(raw)

            await emit(BEGIN)
            started = time.monotonic()
            async for data in ws:
                if isinstance(data, bytes):
                    chunks.append(data)
                    timing.append(time.monotonic() - started)
                elif json.loads(data)["type"] == "Terminate":
                    for message in [
                        {"type": "Turn", "turn_order": 2, "speaker_label": "B",
                         "end_of_turn": True, "transcript": "Thank you."},
                        {"type": "Turn", "turn_order": 1, "speaker_label": "PENDING",
                         "end_of_turn": True, "transcript": "Hello [PERSON_NAME]."},
                        {"type": "Turn", "turn_order": 1, "speaker_label": "PENDING",
                         "end_of_turn": True, "transcript": "Hello, [PERSON_NAME]."},
                        {"type": "SpeakerRevision", "revisions": [
                            {"turn_order": 1, "speaker_label": "B"}]},
                        {"type": "FutureEvent", "unknown": {"preserve": [1, 2, 3]}},
                        {"type": "SpeakerRevision", "revisions": [
                            {"turn_order": 1, "speaker_label": "A"}]},
                        {"type": "Termination"},
                    ]:
                        await emit(message)
                    return

        result, audit = await self.run_session(handler)
        self.assertEqual(audit, b"".join(sent))
        self.assertEqual([len(c) for c in chunks], [1600, 1600, 800])
        self.assertEqual(b"".join(chunks), b"\x00\x00" * 2000)
        self.assertGreaterEqual(timing[-1], 0.12)
        self.assertEqual(result, {"turns": [
            {"speaker": "A", "text": "Hello, [PERSON_NAME]."},
            {"speaker": "B", "text": "Thank you."},
        ]})

    async def test_refuses_audio_without_confirmed_redaction(self):
        async def handler(ws):
            await ws.send(json.dumps({"type": "Begin", "configuration": {}}))
            self.assertIsNone(await ws.wait_closed())

        with self.assertRaises(app.SessionError):
            await self.run_session(handler)

    async def test_disconnect_without_termination_is_failure(self):
        async def handler(ws):
            await ws.send(json.dumps(BEGIN))
            await ws.close()

        with self.assertRaises(Exception):
            await self.run_session(handler)

    def test_partials_are_rejected(self):
        with self.assertRaises(app.SessionError):
            app.Collector().accept({"type": "Turn", "end_of_turn": False})


if __name__ == "__main__":
    unittest.main()
