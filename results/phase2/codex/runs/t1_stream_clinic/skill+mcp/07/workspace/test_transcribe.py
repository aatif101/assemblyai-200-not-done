import asyncio
import io
import json
import time
import unittest
import wave
from urllib.parse import parse_qs, urlsplit

from websockets.asyncio.server import serve

from transcribe import MODEL, TranscriptionError, stream


def wav_audio():
    memory = io.BytesIO()
    with wave.open(memory, "wb") as audio:
        audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        audio.writeframes(b"\0\0" * 1700)  # A short last chunk needs padding.
    memory.seek(0)
    return wave.open(memory, "rb")


def begin(**overrides):
    config = dict(model=MODEL, domain="medical-v1", speaker_labels=True, redact_pii=True)
    config.update(overrides)
    return {"type": "Begin", "configuration": config}


class StreamingTests(unittest.IsolatedAsyncioTestCase):
    async def test_replay_audit_and_final_revisions(self):
        sent = []
        received = []
        times = []

        async def handler(ws):
            params = parse_qs(urlsplit(ws.request.path).query)
            self.assertEqual(params["include_partial_turns"], ["false"])
            self.assertEqual(params["redact_pii"], ["true"])
            self.assertEqual(params["domain"], ["medical-v1"])
            self.assertEqual(ws.request.headers["Authorization"], "test-key")

            async def emit(message):
                # Deliberate unusual spacing and unknown fields: log must be exact.
                raw = json.dumps(message, separators=(", ", ":  "))
                sent.append(raw)
                await ws.send(raw)

            await emit(begin())
            async for data in ws:
                if isinstance(data, bytes):
                    received.append(data)
                    times.append(time.monotonic())
                    if len(received) == 1:
                        await emit({"type": "Turn", "turn_order": 1, "end_of_turn": True,
                                    "speaker_label": "PENDING", "transcript": "Call [PHONE_NUMBER]."})
                        await emit({"type": "Turn", "turn_order": 0, "end_of_turn": True,
                                    "speaker_label": "A", "transcript": "I'm [PERSON_NAME].", "extra": 7})
                else:
                    self.assertEqual(json.loads(data), {"type": "Terminate"})
                    await emit({"type": "SpeakerRevision", "revisions": [
                        {"turn_order": 0, "speaker_label": "B"}]})
                    await emit({"type": "SpeakerRevision", "revisions": [
                        {"turn_order": 0, "speaker_label": "A"},
                        {"turn_order": 1, "speaker_label": "B"}]})
                    await emit({"type": "Termination"})
                    return

        audit = io.BytesIO()
        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            with wav_audio() as audio:
                result = await stream(audio, audit, "test-key", f"ws://127.0.0.1:{port}/v3/ws")
        self.assertEqual(result, {"turns": [
            {"speaker": "A", "text": "I'm [PERSON_NAME]."},
            {"speaker": "B", "text": "Call [PHONE_NUMBER]."}]})
        self.assertEqual(audit.getvalue(), ("\n".join(sent) + "\n").encode())
        self.assertEqual([len(chunk) for chunk in received], [1600, 1600, 1600])
        self.assertGreaterEqual(times[-1] - times[0], 0.095)

    async def test_unconfirmed_privacy_sends_no_audio(self):
        received = []

        async def handler(ws):
            await ws.send(json.dumps(begin(redact_pii=False)))
            async for data in ws:
                received.append(data)

        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            with wav_audio() as audio:
                with self.assertRaises(TranscriptionError):
                    await stream(audio, io.BytesIO(), "test", f"ws://127.0.0.1:{port}")
        self.assertEqual(received, [])

    async def test_error_is_audited_and_fails(self):
        audit = io.BytesIO()
        raw_error = '{"type":"Error","error":"test service failure"}'

        async def handler(ws):
            await ws.send(json.dumps(begin()))
            await ws.recv()
            await ws.send(raw_error)
            await ws.wait_closed()

        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            with wav_audio() as audio:
                with self.assertRaises(TranscriptionError):
                    await stream(audio, audit, "test", f"ws://127.0.0.1:{port}")
        self.assertTrue(audit.getvalue().endswith((raw_error + "\n").encode()))

    async def test_closed_without_termination_fails(self):
        async def handler(ws):
            await ws.send(json.dumps(begin()))
            await ws.recv()
            await ws.close()

        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            with wav_audio() as audio:
                with self.assertRaises(Exception):
                    await stream(audio, io.BytesIO(), "test", f"ws://127.0.0.1:{port}")


if __name__ == "__main__":
    unittest.main()
