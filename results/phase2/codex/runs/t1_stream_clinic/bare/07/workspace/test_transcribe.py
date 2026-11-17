"""Offline protocol tests; no patient recordings or API credentials required."""

import asyncio
import io
import json
import unittest
import wave
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from websockets.asyncio.server import serve

import transcribe as app


def recording(frames=3200):
    data = io.BytesIO()
    with wave.open(data, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\x00\x00" * frames)
    data.seek(0)
    return wave.open(data, "rb")


BEGIN = {
    "type": "Begin",
    "configuration": {"domain": "medical-v1", "redact_pii": True, "speaker_labels": True},
}


class StreamingTests(unittest.IsolatedAsyncioTestCase):
    async def session(self, handler, frames=3200):
        audit = io.BytesIO()
        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            with patch.object(app, "ENDPOINT", f"ws://127.0.0.1:{port}/v3/ws"):
                with recording(frames) as audio:
                    result = await app.stream(audio, audit, "test-key")
        return result, audit.getvalue()

    async def test_pacing_privacy_final_updates_and_exact_audit(self):
        messages = []
        received = []
        query = {}

        async def handler(ws):
            query.update(parse_qs(urlsplit(ws.request.path).query))
            async def emit(event):
                # Deliberately non-default JSON spacing: bytes must survive.
                raw = json.dumps(event, separators=(", ", " : "))
                messages.append(raw.encode() + b"\n")
                await ws.send(raw)
            await emit(BEGIN)
            started = asyncio.get_running_loop().time()
            async for data in ws:
                if isinstance(data, bytes):
                    received.append((asyncio.get_running_loop().time() - started, data))
                    continue
                self.assertEqual(json.loads(data), {"type": "Terminate"})
                # Deliberately out of order, repeated, and partial messages.
                await emit({"type": "Turn", "turn_order": 1, "transcript": "Later.", "end_of_turn": True, "speaker_label": "B"})
                await emit({"type": "Turn", "turn_order": 0, "transcript": "partial", "end_of_turn": False})
                await emit({"type": "Turn", "turn_order": 0, "transcript": "[PERSON_NAME] takes metformin", "end_of_turn": True, "speaker_label": "A"})
                await emit({"type": "Turn", "turn_order": 0, "transcript": "[PERSON_NAME] takes metformin.", "end_of_turn": True, "turn_is_formatted": True, "speaker_label": "A"})
                await emit({"type": "SpeakerRevision", "revisions": [{"turn_order": 1, "speaker_label": "C"}]})
                await emit({"type": "Termination"})
                break

        result, audit = await self.session(handler)
        self.assertEqual(audit, b"".join(messages))
        self.assertEqual(result, {"turns": [
            {"speaker": "A", "text": "[PERSON_NAME] takes metformin."},
            {"speaker": "C", "text": "Later."},
        ]})
        self.assertEqual(len(received), 2)
        self.assertEqual(sum(len(data) for _, data in received), 6400)
        self.assertGreaterEqual(received[0][0], 0.09)
        self.assertGreaterEqual(received[-1][0], 0.19)
        for name in ["speaker_labels", "redact_pii"]:
            self.assertEqual(query[name], ["true"])
        self.assertEqual(query["domain"], ["medical-v1"])
        self.assertEqual(query["mode"], ["max_accuracy"])
        self.assertEqual(query["include_partial_turns"], ["false"])
        policies = json.loads(query["redact_pii_policies"][0])
        self.assertTrue({"person_name", "phone_number", "us_social_security_number"}.issubset(policies))
        self.assertTrue({"drug", "medical_condition", "medical_process"}.isdisjoint(policies))

    async def test_unconfirmed_redaction_sends_no_audio(self):
        received = []
        async def handler(ws):
            await ws.send(json.dumps({"type": "Begin", "configuration": {"redact_pii": False}}))
            async for data in ws:
                received.append(data)
        with self.assertRaisesRegex(app.TranscriptionError, "no audio sent"):
            await self.session(handler)
        self.assertEqual(received, [])

    async def test_warning_fails_without_printing_server_content(self):
        async def handler(ws):
            await ws.send(json.dumps({"type": "Warning", "warning": "sensitive server details"}))
            await ws.wait_closed()
        with self.assertRaises(app.TranscriptionError) as caught:
            await self.session(handler)
        self.assertNotIn("sensitive", str(caught.exception))

    async def test_premature_termination_fails(self):
        async def handler(ws):
            await ws.send(json.dumps(BEGIN))
            await ws.send('{"type":"Termination"}')
            await ws.wait_closed()
        with self.assertRaisesRegex(app.TranscriptionError, "before all audio"):
            await self.session(handler)

    async def test_disconnect_is_not_success(self):
        async def handler(ws):
            await ws.send(json.dumps(BEGIN))
            await ws.close()
        from websockets.exceptions import ConnectionClosed
        with self.assertRaises(ConnectionClosed):
            await self.session(handler)


class TurnTests(unittest.TestCase):
    def test_silence_and_unfinished_turns(self):
        turns = app.Turns()
        turns.receive({"type": "Turn", "turn_order": 0, "transcript": "unfinished", "end_of_turn": False})
        self.assertEqual(turns.output(), {"turns": []})

    def test_formatted_final_cannot_be_replaced_by_unformatted(self):
        turns = app.Turns()
        turns.receive({"type": "Turn", "turn_order": 0, "transcript": "Hello.", "end_of_turn": True, "turn_is_formatted": True, "words": [{"speaker": "A"}]})
        turns.receive({"type": "Turn", "turn_order": 0, "transcript": "hello", "end_of_turn": True})
        self.assertEqual(turns.output(), {"turns": [{"speaker": "A", "text": "Hello."}]})


if __name__ == "__main__":
    unittest.main()
