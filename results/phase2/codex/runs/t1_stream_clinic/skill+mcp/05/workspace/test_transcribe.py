import asyncio
import io
import json
import time
import unittest
from urllib.parse import parse_qs, urlsplit
import wave

from websockets.asyncio.server import serve

import transcribe as app


def recording(frames=1700):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setparams((1, 2, 16000, frames, "NONE", "not compressed"))
        audio.writeframes(b"\x01\x02" * frames)
    buffer.seek(0)
    return wave.open(buffer, "rb")


def begin(**overrides):
    config = dict(model=app.PARAMETERS["speech_model"], domain="medical-v1",
                  speaker_labels=True, redact_pii=True)
    config.update(overrides)
    return json.dumps({"type": "Begin", "configuration": config})


class StreamingTests(unittest.IsolatedAsyncioTestCase):
    async def test_stream_pacing_audit_finals_and_revisions(self):
        messages = [
            begin(),
            '{ "type": "Turn", "turn_order": 1, "transcript": "[PERSON_NAME].", '
            '"end_of_turn": true, "speaker_label": "PENDING" }',
            json.dumps(dict(type="Turn", turn_order=0, transcript="Hello.",
                            end_of_turn=True, speaker_label="A")),
            json.dumps(dict(type="SpeakerRevision", revisions=[
                dict(turn_order=1, speaker_label="A")])),
            json.dumps(dict(type="SpeakerRevision", revisions=[
                dict(turn_order=1, speaker_label="B")])),
            '{"type":"Termination","audio_duration_seconds":0.15}',
        ]
        packets, times, server_errors = [], [], []

        async def handler(ws):
            try:
                query = parse_qs(urlsplit(ws.request.path).query)
                self.assertEqual(query["include_partial_turns"], ["false"])
                self.assertEqual(query["redact_pii"], ["true"])
                self.assertEqual(ws.request.headers["Authorization"], "test-key")
                await ws.send(messages[0])
                async for payload in ws:
                    if isinstance(payload, bytes):
                        packets.append(payload)
                        times.append(time.monotonic())
                        if len(packets) == 1:
                            # Prove receiving proceeds during audio streaming.
                            await ws.send(messages[1])
                    else:
                        self.assertEqual(json.loads(payload), {"type": "Terminate"})
                        self.assertEqual(len(packets), 3)
                        for message in messages[2:]:
                            await ws.send(message)
                        # The client must close after Termination, without
                        # requiring the server to close the transport first.
                        await ws.wait_closed()
                        break
            except Exception as exc:
                server_errors.append(exc)

        audit = io.BytesIO()
        with recording() as audio:
            async with serve(handler, "127.0.0.1", 0) as server:
                port = server.sockets[0].getsockname()[1]
                result = await app.stream(audio, audit, "test-key", f"ws://127.0.0.1:{port}")
        self.assertFalse(server_errors, server_errors)
        self.assertEqual(result, {"turns": [
            {"speaker": "A", "text": "Hello."},
            {"speaker": "B", "text": "[PERSON_NAME]."},
        ]})
        self.assertEqual(audit.getvalue(), ("\n".join(messages) + "\n").encode())
        self.assertTrue(all(len(packet) == 1600 for packet in packets))
        self.assertEqual(packets[-1], b"\x01\x02" * 100 + b"\0" * 1400)
        self.assertTrue(all(b - a >= 0.045 for a, b in zip(times, times[1:])))

    async def test_errors_and_early_close_fail_with_audit_preserved(self):
        cases = [
            [begin(redact_pii=False)],
            [begin(), '{"type":"Error","error":"test"}'],
            [begin()],  # Normal close without Termination still fails.
            [begin(), '{"type":"Turn","end_of_turn":false,"transcript":"draft"}'],
        ]
        for messages in cases:
            with self.subTest(messages=messages):
                async def handler(ws):
                    for message in messages:
                        await ws.send(message)
                    await ws.close()

                audit = io.BytesIO()
                with recording(16000) as audio:
                    async with serve(handler, "127.0.0.1", 0) as server:
                        port = server.sockets[0].getsockname()[1]
                        with self.assertRaises(Exception):
                            await app.stream(audio, audit, "test-key", f"ws://127.0.0.1:{port}")
                self.assertEqual(audit.getvalue(), ("\n".join(messages) + "\n").encode())


if __name__ == "__main__":
    unittest.main()
