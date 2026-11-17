import asyncio
import io
import json
import unittest
import wave
from unittest.mock import patch

from websockets.asyncio.server import serve

import transcribe


class StreamingTests(unittest.IsolatedAsyncioTestCase):
    async def test_stream_pacing_audit_final_replacement_and_revision(self):
        received = []
        sent = []

        async def server(ws):
            async def emit(event):
                raw = json.dumps(event, separators=(", ", ": "))
                sent.append(raw)
                await ws.send(raw)

            self.assertIn("include_partial_turns=false", ws.request.path)
            self.assertIn("domain=medical-v1", ws.request.path)
            self.assertIn("redact_pii=true", ws.request.path)
            self.assertEqual(ws.request.headers["Authorization"], "test-key")
            await emit({"type": "Begin", "configuration": {
                "model": transcribe.MODEL, "domain": "medical-v1",
                "speaker_labels": True, "redact_pii": True,
            }})
            async for data in ws:
                if isinstance(data, bytes):
                    received.append((asyncio.get_running_loop().time(), data))
                    if len(received) == 1:
                        await emit({"type": "Turn", "turn_order": 0, "end_of_turn": True,
                                    "speaker_label": "PENDING", "transcript": "Call [PERSON_NAME]."})
                else:
                    self.assertEqual(json.loads(data), {"type": "Terminate"})
                    await emit({"type": "Turn", "turn_order": 0, "end_of_turn": True,
                                "speaker_label": "A", "transcript": "Call [PERSON_NAME]!"})
                    await emit({"type": "SpeakerRevision", "revisions": [
                        {"turn_order": 0, "speaker_label": "B", "words": []}]})
                    await emit({"type": "Termination"})
                    break

        source = io.BytesIO()
        with wave.open(source, "wb") as audio:
            audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            audio.writeframes(b"\x01\x02" * 1700)
        source.seek(0)
        frames = io.BytesIO()
        async with serve(server, "127.0.0.1", 0) as listener:
            port = listener.sockets[0].getsockname()[1]
            with patch.object(transcribe, "ENDPOINT", f"ws://127.0.0.1:{port}/v3/ws"):
                with wave.open(source, "rb") as audio:
                    result = await transcribe.stream(audio, frames, "test-key")
        self.assertEqual(result, {"turns": [{"speaker": "B", "text": "Call [PERSON_NAME]!"}]})
        self.assertEqual(frames.getvalue(), ("\n".join(sent) + "\n").encode())
        self.assertEqual(len(received), 3)
        self.assertTrue(all(len(data) == 1600 for _, data in received))
        self.assertTrue(received[0][1].startswith(b"\x01\x02"))
        self.assertGreaterEqual(received[-1][0] - received[0][0], 0.095)

    async def test_server_error_is_logged_unmodified(self):
        class Socket:
            async def recv(self):
                return '{ "type": "Error", "error": "test" }'
        frames = io.BytesIO()
        with self.assertRaises(transcribe.TranscriptionError):
            await transcribe.receive(Socket(), frames)
        self.assertEqual(frames.getvalue(), b'{ "type": "Error", "error": "test" }\n')

    async def test_reject_unconfirmed_privacy_settings(self):
        with self.assertRaises(transcribe.TranscriptionError):
            transcribe.validate_begin({"type": "Begin", "configuration": {"model": transcribe.MODEL}})


if __name__ == "__main__":
    unittest.main()
