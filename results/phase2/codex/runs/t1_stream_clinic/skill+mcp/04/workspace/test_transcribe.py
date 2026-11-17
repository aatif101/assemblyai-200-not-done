import asyncio
import json
from pathlib import Path
import tempfile
import time
import unittest
from urllib.parse import parse_qs, urlsplit
import wave

from websockets.asyncio.server import serve

from transcribe import MODEL, TranscriptionError, transcribe


class StreamingTests(unittest.IsolatedAsyncioTestCase):
    async def test_server_error_and_premature_close_do_not_publish_output(self):
        for error_frame in (True, False):
            with self.subTest(error_frame=error_frame):
                async def handler(ws):
                    await ws.send(json.dumps({"type": "Begin", "configuration": {
                        "model": MODEL, "domain": "medical-v1",
                        "speaker_labels": True, "redact_pii": True}}))
                    await ws.recv()
                    if error_frame:
                        await ws.send('{"type":"Error","error":"test failure"}')
                    await ws.close()

                with tempfile.TemporaryDirectory() as directory:
                    source = Path(directory) / "input.wav"
                    output = Path(directory) / "out.json"
                    with wave.open(str(source), "wb") as audio:
                        audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                        audio.writeframes(b"\0" * 16000)
                    async with serve(handler, "127.0.0.1", 0) as server:
                        port = server.sockets[0].getsockname()[1]
                        with self.assertRaises(Exception):
                            await transcribe(source, output, "test", f"ws://127.0.0.1:{port}/v3/ws")
                    self.assertFalse(output.exists())
                    self.assertTrue(Path(str(output) + ".frames.jsonl").exists())

    async def test_pacing_audit_final_turns_and_revisions(self):
        messages = [
            {"type": "Begin", "configuration": {"model": MODEL,
             "domain": "medical-v1", "speaker_labels": True, "redact_pii": True}},
            {"type": "Turn", "turn_order": 1, "end_of_turn": True,
             "speaker_label": "B", "transcript": "Call [PHONE_NUMBER]."},
            {"type": "Turn", "turn_order": 0, "end_of_turn": True,
             "speaker_label": "PENDING", "transcript": "[PERSON_NAME] takes metformin."},
            {"type": "SpeakerRevision", "revisions": [
                {"turn_order": 0, "speaker_label": "B", "words": []}]},
            {"type": "SpeakerRevision", "revisions": [
                {"turn_order": 0, "speaker_label": "A", "words": []}]},
            {"type": "Termination"},
        ]
        raw = [json.dumps(m, separators=(",", ":")) for m in messages]
        chunks, arrivals = [], []

        async def handler(ws):
            query = parse_qs(urlsplit(ws.request.path).query)
            self.assertEqual(query["include_partial_turns"], ["false"])
            self.assertEqual(query["redact_pii"], ["true"])
            self.assertNotIn("redact_pii_policies", query)
            self.assertEqual(ws.request.headers["Authorization"], "test-key")
            started = time.monotonic()
            await ws.send(raw[0])
            async for payload in ws:
                if isinstance(payload, bytes):
                    chunks.append(payload)
                    arrivals.append(time.monotonic() - started)
                    if len(chunks) == 1:
                        await ws.send(raw[1])
                else:
                    self.assertEqual(json.loads(payload), {"type": "Terminate"})
                    for item in raw[2:]:
                        await ws.send(item)
                    break

        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory) / "input.wav", Path(directory) / "out.json"
            pcm = b"\x01\x02" * 1700  # 106.25ms, padded to 150ms.
            with wave.open(str(source), "wb") as audio:
                audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                audio.writeframes(pcm)
            async with serve(handler, "127.0.0.1", 0) as server:
                port = server.sockets[0].getsockname()[1]
                await transcribe(source, output, "test-key", f"ws://127.0.0.1:{port}/v3/ws")
            self.assertEqual(len(chunks), 3)
            self.assertEqual(b"".join(chunks), pcm.ljust(4800, b"\0"))
            self.assertGreaterEqual(arrivals[-1], 0.145)
            self.assertTrue(all(b - a >= 0.045 for a, b in zip(arrivals, arrivals[1:])))
            self.assertEqual(Path(str(output) + ".frames.jsonl").read_text(), "\n".join(raw) + "\n")
            self.assertEqual(json.loads(output.read_text()), {"turns": [
                {"speaker": "A", "text": "[PERSON_NAME] takes metformin."},
                {"speaker": "B", "text": "Call [PHONE_NUMBER]."}]})

    async def test_required_configuration_is_checked_before_sending_audio(self):
        async def handler(ws):
            await ws.send(json.dumps({"type": "Begin", "configuration": {"model": MODEL}}))
            received = [payload async for payload in ws]
            self.assertEqual(received, [])

        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory) / "input.wav", Path(directory) / "out.json"
            with wave.open(str(source), "wb") as audio:
                audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                audio.writeframes(b"\0" * 1600)
            async with serve(handler, "127.0.0.1", 0) as server:
                port = server.sockets[0].getsockname()[1]
                with self.assertRaises(TranscriptionError):
                    await transcribe(source, output, "test", f"ws://127.0.0.1:{port}/v3/ws")
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
