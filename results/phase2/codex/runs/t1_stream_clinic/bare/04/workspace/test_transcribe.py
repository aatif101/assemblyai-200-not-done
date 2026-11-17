import asyncio
import io
import json
import time
import unittest
import wave
from unittest.mock import patch

from websockets.asyncio.server import serve

import transcribe


class TurnTests(unittest.TestCase):
    def test_final_replacements_and_speaker_revisions(self):
        turns = transcribe.Turns()
        turns.accept(dict(type="Turn", turn_order=2, end_of_turn=False, transcript="partial"))
        turns.accept(dict(type="Turn", turn_order=1, end_of_turn=True,
                          transcript="hello", speaker_label="A"))
        turns.accept(dict(type="Turn", turn_order=1, end_of_turn=True,
                          turn_is_formatted=True, transcript="Hello.", speaker_label="A"))
        turns.accept(dict(type="Turn", turn_order=1, end_of_turn=True,
                          transcript="stale", speaker_label="A"))
        turns.accept(dict(type="SpeakerRevision", revisions=[dict(turn_order=1, speaker_label="B")]))
        self.assertEqual(turns.result(), {"turns": [{"speaker": "B", "text": "Hello."}]})


class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self, error=False):
        received = []
        sent = []
        begin = '{ "type": "Begin", "configuration": {"redact_pii":true,"speaker_labels":true,"domain":"medical-v1"} }'
        turn = '{"type":"Turn","turn_order":0,"end_of_turn":true,"speaker_label":"A","transcript":"[PERSON_NAME] takes metformin."}'
        failure = '{"type":"Error","error":"synthetic failure"}'
        termination = '{"type":"Termination"}'

        async def handler(ws):
            sent.append(begin)
            await ws.send(begin)
            if error:
                sent.append(failure)
                await ws.send(failure)
                await ws.wait_closed()
                return
            async for message in ws:
                if isinstance(message, bytes):
                    received.append((time.monotonic(), message))
                else:
                    self.assertEqual(json.loads(message), {"type": "Terminate"})
                    for frame in (turn, termination):
                        sent.append(frame)
                        await ws.send(frame)
                    return

        audio_bytes = io.BytesIO()
        with wave.open(audio_bytes, "wb") as wav:
            wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            wav.writeframes(b"\0" * 8000)  # 250 ms, padded to 300 ms
        audio_bytes.seek(0)
        audit = io.StringIO()
        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            with wave.open(audio_bytes) as audio, patch.object(
                transcribe, "connection_url", return_value=f"ws://127.0.0.1:{port}"
            ):
                started = time.monotonic()
                if error:
                    with self.assertRaises(transcribe.TranscriptionError):
                        await transcribe.stream(audio, audit, "test-key")
                    result = None
                else:
                    result = await transcribe.stream(audio, audit, "test-key")
                elapsed = time.monotonic() - started
        self.assertEqual(audit.getvalue(), "".join(frame + "\n" for frame in sent))
        return result, received, elapsed

    async def test_pacing_audit_and_final_drain(self):
        result, received, elapsed = await self.exercise()
        self.assertEqual(result, {"turns": [{"speaker": "A", "text": "[PERSON_NAME] takes metformin."}]})
        self.assertEqual([len(chunk) for _, chunk in received], [3200, 3200, 3200])
        self.assertGreaterEqual(elapsed, 0.29)
        for (before, _), (after, _) in zip(received, received[1:]):
            self.assertGreater(after - before, 0.08)

    async def test_service_error_stops_audio_and_preserves_audit(self):
        _, received, _ = await self.exercise(error=True)
        self.assertEqual(received, [])
