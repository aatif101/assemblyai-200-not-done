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
    data = io.BytesIO()
    with wave.open(data, "wb") as w:
        w.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        w.writeframes(b"\0" * 4800)  # 150ms
    data.seek(0)
    return wave.open(data, "rb")


class StreamingTests(unittest.IsolatedAsyncioTestCase):
    async def run_session(self, handler):
        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            with patch.object(app, "ENDPOINT", f"ws://127.0.0.1:{port}/v3/ws"):
                with audio_file() as audio:
                    self.audit = io.StringIO()
                    return await app.stream(audio, self.audit, "test-key")

    async def test_pacing_raw_frames_final_text_and_revisions(self):
        messages = [
            json.dumps(BEGIN),
            '{ "type": "Heartbeat", "unknown_field": 42 }',
            json.dumps({"type": "Turn", "turn_order": 1, "end_of_turn": True,
                        "speaker_label": "PENDING", "transcript": "I take metformin."}),
            json.dumps({"type": "Turn", "turn_order": 0, "end_of_turn": True,
                        "speaker_label": "A", "transcript": "Hello [PERSON_NAME]."}),
            json.dumps({"type": "Turn", "turn_order": 1, "end_of_turn": True,
                        "speaker_label": "A", "transcript": "I take metformin daily."}),
            json.dumps({"type": "SpeakerRevision", "revisions": [
                {"turn_order": 1, "speaker_label": "B"}]}),
            json.dumps({"type": "SpeakerRevision", "revisions": [
                {"turn_order": 0, "speaker_label": "B"}]}),
            json.dumps({"type": "Termination"}),
        ]
        received_audio = []
        elapsed = []

        async def handler(ws):
            params = parse_qs(urlsplit(ws.request.path).query)
            self.assertEqual(params["include_partial_turns"], ["false"])
            self.assertEqual(params["redact_pii"], ["true"])
            self.assertEqual(params["domain"], ["medical-v1"])
            policies = set(json.loads(params["redact_pii_policies"][0]))
            self.assertTrue({"person_name", "phone_number", "us_social_security_number",
                             "date_of_birth", "email_address", "healthcare_number",
                             "location_address"}.issubset(policies))
            self.assertTrue(policies.isdisjoint({"drug", "medical_condition", "medical_process"}))
            self.assertEqual(ws.request.headers["Authorization"], "test-key")
            await ws.send(messages[0])
            start = time.monotonic()
            while True:
                frame = await ws.recv()
                if isinstance(frame, str):
                    self.assertEqual(json.loads(frame), {"type": "Terminate"})
                    break
                received_audio.append(frame)
                elapsed.append(time.monotonic() - start)
            for message in messages[1:]:
                await ws.send(message)

        result = await self.run_session(handler)
        self.assertEqual(result, {"turns": [
            {"speaker": "B", "text": "Hello [PERSON_NAME]."},
            {"speaker": "B", "text": "I take metformin daily."},
        ]})
        self.assertEqual(self.audit.getvalue(), "".join(m + "\n" for m in messages))
        self.assertEqual([len(x) for x in received_audio], [1600] * 3)
        self.assertGreaterEqual(elapsed[-1], .145)
        self.assertGreaterEqual(elapsed[0], .045)

    async def test_disabled_redaction_sends_no_audio(self):
        received = []

        async def handler(ws):
            begin = {"type": "Begin", "configuration": {**BEGIN["configuration"], "redact_pii": False}}
            await ws.send(json.dumps(begin))
            async for frame in ws:
                received.append(frame)

        with self.assertRaises(app.TranscriptionError):
            await self.run_session(handler)
        self.assertEqual(received, [])

    async def test_closed_socket_without_termination_fails(self):
        async def handler(ws):
            await ws.send(json.dumps(BEGIN))
            await ws.close()

        with self.assertRaises(Exception):
            await self.run_session(handler)

    async def test_provider_error_is_logged_and_fails(self):
        async def handler(ws):
            await ws.send(json.dumps({"type": "Error", "error": "test error"}))
            await ws.wait_closed()

        with self.assertRaises(app.TranscriptionError):
            await self.run_session(handler)
        self.assertEqual(json.loads(self.audit.getvalue())["type"], "Error")


if __name__ == "__main__":
    unittest.main()
