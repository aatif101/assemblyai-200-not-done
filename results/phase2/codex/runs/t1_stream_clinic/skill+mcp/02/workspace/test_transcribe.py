"""Protocol tests use a local WebSocket server, with no API usage."""
import asyncio
import io
import json
import tempfile
import time
import unittest
import wave
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from websockets.asyncio.server import serve
import transcribe as app


class StreamingTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self, bad_config=False, early_close=False):
        received = []
        sent = []
        audio_chunks = []
        query = {}
        config = {"model": app.MODEL, "domain": "medical-v1", "speaker_labels": True,
                  "redact_pii": not bad_config}

        async def server(ws):
            query.update(parse_qs(urlsplit(ws.request.path).query))

            async def emit(frame):
                raw = json.dumps(frame, separators=(",", ": "))
                sent.append(raw.encode() + b"\n")
                await ws.send(raw)

            await emit({"type": "Begin", "configuration": config})
            if bad_config:
                async for item in ws:
                    received.append(item)
                return
            if early_close:
                return
            async for item in ws:
                received.append(item)
                if isinstance(item, bytes):
                    audio_chunks.append((time.monotonic(), item))
                    if len(audio_chunks) == 1:
                        await emit({"type": "Turn", "turn_order": 1, "end_of_turn": True,
                                    "speaker_label": "PENDING", "transcript": "[PERSON_NAME] called."})
                        await emit({"type": "Turn", "turn_order": 0, "end_of_turn": True,
                                    "speaker_label": "A", "transcript": "Hello."})
                elif json.loads(item)["type"] == "Terminate":
                    await emit({"type": "SpeakerRevision", "revisions": [
                        {"turn_order": 1, "speaker_label": "B", "words": []}]})
                    await emit({"type": "Termination"})
                    return

        with tempfile.TemporaryFile() as audio_file:
            with wave.open(audio_file, "wb") as wav:
                wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                wav.writeframes(b"\0" * 6720)  # 210ms; last chunk needs padding.
            audio_file.seek(0)
            with wave.open(audio_file, "rb") as wav:
                async with serve(server, "127.0.0.1", 0) as service:
                    endpoint = f"ws://127.0.0.1:{service.sockets[0].getsockname()[1]}"
                    audit = io.BytesIO()
                    with patch.object(app, "ENDPOINT", endpoint):
                        if bad_config or early_close:
                            with self.assertRaises(Exception):
                                await app.transcribe(wav, "test-key", audit)
                            result = None
                        else:
                            result = await app.transcribe(wav, "test-key", audit)
        self.assertEqual(audit.getvalue(), b"".join(sent))
        self.assertEqual(query["include_partial_turns"], ["false"])
        self.assertEqual(query["redact_pii"], ["true"])
        self.assertNotIn("redact_pii_policies", query)
        return result, audio_chunks, received

    async def test_stream_pacing_raw_audit_and_final_revision(self):
        result, chunks, _ = await self.exercise()
        self.assertEqual(result, {"turns": [{"speaker": "A", "text": "Hello."},
                                             {"speaker": "B", "text": "[PERSON_NAME] called."}]})
        self.assertEqual(len(chunks), 5)
        self.assertTrue(all(len(data) == 1600 for _, data in chunks))
        self.assertGreaterEqual(chunks[-1][0] - chunks[0][0], .18)

    async def test_reject_unconfirmed_redaction_before_audio(self):
        _, _, received = await self.exercise(bad_config=True)
        self.assertEqual(received, [])

    async def test_premature_close_is_failure(self):
        await self.exercise(early_close=True)

    def test_error_and_partial_are_failures(self):
        for frame in ({"type": "Error", "error": "test"},
                      {"type": "Turn", "end_of_turn": False}):
            with self.assertRaises(app.TranscriptionError):
                app.Turns().accept(frame)


if __name__ == "__main__":
    unittest.main()
