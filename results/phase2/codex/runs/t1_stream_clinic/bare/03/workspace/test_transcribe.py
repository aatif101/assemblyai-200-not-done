import asyncio
import io
import json
import unittest
from unittest.mock import patch

from websockets.asyncio.client import connect as real_connect
from websockets.asyncio.server import serve

import transcribe


BEGIN = '{"type":"Begin", "configuration":{"redact_pii":true,"speaker_labels":true,"domain":"medical-v1"}}'


class StreamingTests(unittest.IsolatedAsyncioTestCase):
    async def test_short_final_audio_packet_is_padded(self):
        packets = []

        class Audio:
            def __init__(self):
                self.data = [b"\x01\x00" * 100, b""]

            def readframes(self, count):
                return self.data.pop(0)

        class Socket:
            async def send(self, packet):
                packets.append(packet)

        begun = asyncio.Event()
        begun.set()
        with patch.object(transcribe.asyncio, "sleep", new=unittest.mock.AsyncMock()):
            await transcribe.send_audio(Socket(), Audio(), begun)
        self.assertEqual(packets[0], b"\x01\x00" * 100 + b"\x00" * 3000)
        self.assertTrue(all(len(p) == 3200 for p in packets if isinstance(p, bytes)))

    async def exercise(self, handler):
        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]

            def connection(uri, **kwargs):
                self.assertIn("redact_pii=true", uri)
                self.assertIn("domain=medical-v1", uri)
                self.assertIn("speaker_labels=true", uri)
                self.assertIn("include_partial_turns=false", uri)
                return real_connect(f"ws://127.0.0.1:{port}")

            audio = io.BytesIO(b"\x01\x00" * 3200)
            # send_audio uses wave.readframes, so wrap raw PCM for this fixture.
            class Audio:
                def readframes(self, count):
                    return audio.read(count * 2)

            audit = io.BytesIO()
            with patch.object(transcribe, "connect", connection):
                result = await transcribe.stream(Audio(), audit, "test-key")
            return result, audit.getvalue()

    async def test_pacing_raw_audit_final_replacements_and_speaker_revision(self):
        received = []
        sent = [BEGIN]

        async def handler(ws):
            await ws.send(BEGIN)
            start = asyncio.get_running_loop().time()
            while True:
                raw = await ws.recv()
                if isinstance(raw, bytes):
                    received.append((asyncio.get_running_loop().time() - start, raw))
                elif json.loads(raw)["type"] == "Terminate":
                    break
            events = [
                {"type": "Turn", "turn_order": 1, "end_of_turn": False, "transcript": "partial"},
                {"type": "Turn", "turn_order": 1, "end_of_turn": True, "transcript": "Take metformin", "speaker_label": "A"},
                {"type": "Turn", "turn_order": 1, "end_of_turn": True, "turn_is_formatted": True, "transcript": "Take metformin.", "speaker_label": "A"},
                {"type": "Turn", "turn_order": 0, "end_of_turn": True, "turn_is_formatted": True, "transcript": "[PERSON_NAME]", "speaker_label": "B"},
                {"type": "SpeakerRevision", "revisions": [{"turn_order": 1, "speaker_label": "C"}]},
                {"type": "Termination"},
            ]
            for event in events:
                raw = json.dumps(event, separators=(",", ": "))
                sent.append(raw)
                await ws.send(raw)

        result, audit = await self.exercise(handler)
        self.assertEqual(audit, b"".join(s.encode() + b"\n" for s in sent))
        self.assertEqual(result, {"turns": [
            {"speaker": "B", "text": "[PERSON_NAME]"},
            {"speaker": "C", "text": "Take metformin."},
        ]})
        self.assertEqual(len(received), 12)
        self.assertGreaterEqual(received[1][0], 0.18)
        self.assertGreaterEqual(received[-1][0], 1.18)
        self.assertEqual(received[0][1], b"\x01\x00" * 1600)

    async def test_unconfirmed_redaction_sends_no_audio(self):
        received = []

        async def handler(ws):
            await ws.send('{"type":"Begin","configuration":{"redact_pii":false}}')
            async for raw in ws:
                received.append(raw)

        with self.assertRaisesRegex(transcribe.TranscriptionError, "no audio sent"):
            await self.exercise(handler)
        self.assertEqual(received, [])

    async def test_disconnect_without_termination_fails(self):
        async def handler(ws):
            await ws.send(BEGIN)
            await ws.recv()
            await ws.close()

        with self.assertRaisesRegex(transcribe.TranscriptionError, "without a Termination"):
            await self.exercise(handler)

    async def test_provider_error_fails(self):
        async def handler(ws):
            await ws.send('{"type":"Error","error":"test rejection"}')
            await ws.wait_closed()

        with self.assertRaisesRegex(transcribe.TranscriptionError, "returned an error"):
            await self.exercise(handler)


if __name__ == "__main__":
    unittest.main()
