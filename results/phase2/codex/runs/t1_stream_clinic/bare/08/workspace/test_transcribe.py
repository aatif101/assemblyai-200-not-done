import asyncio
import io
import json
import time
import unittest
from unittest.mock import patch
import wave

from websockets.asyncio.client import connect as websocket_connect
from websockets.asyncio.server import serve

import transcribe as app


def wav(samples=3200):
    data = io.BytesIO()
    with wave.open(data, "wb") as audio:
        audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        audio.writeframes(b"\x01\x02" * samples)
    data.seek(0)
    return wave.open(data, "rb")


def turn(order, text, formatted=True, final=True, speaker="A"):
    return {"type": "Turn", "turn_order": order, "transcript": text,
            "turn_is_formatted": formatted, "end_of_turn": final,
            "speaker_label": speaker}


BEGIN = json.dumps({"type": "Begin", "configuration": {
    "redact_pii": True, "speaker_labels": True, "domain": "medical-v1"}})


class TurnTests(unittest.TestCase):
    def test_final_text_order_and_speaker_revisions(self):
        turns = app.Turns()
        turns.receive(turn(2, "draft", formatted=False))
        turns.receive(turn(1, "Metformin."))
        turns.receive(turn(2, "[PERSON_NAME]."))
        turns.receive(turn(2, "stale draft", formatted=False))
        turns.receive(turn(3, "unfinished", final=False))
        turns.receive({"type": "SpeakerRevision", "revisions": [
            {"turn_order": 1, "speaker_label": "B"}]})
        self.assertEqual(turns.output(), {"turns": [
            {"speaker": "B", "text": "Metformin."},
            {"speaker": "A", "text": "[PERSON_NAME]."}]})


class StreamingTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_time_pcm_pacing_and_final_short_chunk(self):
        class Socket:
            def __init__(self):
                self.messages = []

            async def send(self, message):
                self.messages.append((time.monotonic(), message))

        ws = Socket()
        started = time.monotonic()
        with wav(3300) as audio:
            await app.send_audio(ws, audio)
        binary = [(t, m) for t, m in ws.messages if isinstance(m, bytes)]
        self.assertEqual([len(m) for _, m in binary], [3200, 3200, 1600])
        self.assertGreaterEqual(binary[0][0] - started, 0.095)
        self.assertGreaterEqual(binary[1][0] - binary[0][0], 0.095)
        self.assertGreaterEqual(binary[-1][0] - started, 3300 / 16000 - 0.005)
        self.assertEqual(json.loads(ws.messages[-1][1]), {"type": "Terminate"})
        self.assertEqual(binary[0][1], b"\x01\x02" * 1600)

    async def test_wire_audit_and_drain_after_terminate(self):
        messages = [BEGIN, '{"type":"Heartbeat", "extra":42}',
                    json.dumps(turn(0, "unformatted", formatted=False)),
                    json.dumps(turn(0, "[PERSON_NAME] takes metformin.")),
                    json.dumps({"type": "SpeakerRevision", "revisions": [
                        {"turn_order": 0, "speaker_label": "B"}]}),
                    '{"type": "Termination", "audio_duration_seconds": 1}']
        received = []

        async def handler(ws):
            await ws.send(messages[0])
            async for raw in ws:
                received.append(raw)
                if isinstance(raw, str) and json.loads(raw)["type"] == "Terminate":
                    for message in messages[1:]:
                        await ws.send(message)
                    break

        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            def local_connect(*args, **kwargs):
                return websocket_connect(f"ws://127.0.0.1:{port}")
            audit = io.StringIO()
            with patch.object(app, "connect", local_connect), wav(1600) as audio:
                result = await app.stream(audio, audit, "test-key")
        self.assertEqual(audit.getvalue(), "".join(m + "\n" for m in messages))
        self.assertEqual(result, {"turns": [
            {"speaker": "B", "text": "[PERSON_NAME] takes metformin."}]})
        self.assertEqual(received[0], b"\x01\x02" * 1600)

    async def test_error_is_audited_without_exposing_error_text(self):
        raw = '{"type":"Error", "error":"private test data"}'
        class Socket:
            async def recv(self):
                return raw
        audit = io.StringIO()
        with self.assertRaises(app.TranscriptionError) as error:
            await app.read_message(Socket(), audit)
        self.assertNotIn("private test data", str(error.exception))
        self.assertEqual(audit.getvalue(), raw + "\n")

    async def test_missing_privacy_confirmation_sends_no_audio(self):
        received = []
        async def handler(ws):
            await ws.send('{"type":"Begin","configuration":{}}')
            async for message in ws:
                received.append(message)
        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            with patch.object(app, "connect", lambda *a, **kw: websocket_connect(f"ws://127.0.0.1:{port}")), wav() as audio:
                with self.assertRaises(app.TranscriptionError):
                    await app.stream(audio, io.StringIO(), "test-key")
        self.assertEqual(received, [])


if __name__ == "__main__":
    unittest.main()
