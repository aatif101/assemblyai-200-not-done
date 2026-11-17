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
    async def exercise(self, fail=False):
        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / "input.wav"
            output = Path(directory) / "output.json"
            pcm = b"\0\0" * 3600  # 225ms, exercises a final short chunk.
            with wave.open(str(audio), "wb") as wav:
                wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                wav.writeframes(pcm)
            messages = []
            received = bytearray()
            times = []

            async def handler(ws):
                query = parse_qs(urlsplit(ws.request.path).query)
                self.assertEqual(query["include_partial_turns"], ["false"])
                self.assertEqual(query["redact_pii"], ["true"])
                self.assertNotIn("redact_pii_policies", query)
                self.assertEqual(query["domain"], ["medical-v1"])
                self.assertEqual(ws.request.headers["Authorization"], "test-key")

                async def emit(obj):
                    raw = json.dumps(obj, separators=(", ", ":  "))
                    messages.append(raw)
                    await ws.send(raw)

                await emit({"type": "Begin", "configuration": {
                    "model": MODEL, "domain": "medical-v1",
                    "speaker_labels": True, "redact_pii": not fail}})
                if fail:
                    await ws.wait_closed()
                    return
                started = time.monotonic()
                async for message in ws:
                    if isinstance(message, bytes):
                        received.extend(message)
                        times.append(time.monotonic() - started)
                        if len(times) == 1:
                            await emit({"type": "Turn", "turn_order": 0,
                                        "end_of_turn": True, "speaker_label": "PENDING",
                                        "transcript": "[PERSON_NAME] takes metformin."})
                    else:
                        self.assertEqual(json.loads(message), {"type": "Terminate"})
                        # Duplicate finals must replace, never duplicate output turns.
                        await emit({"type": "Turn", "turn_order": 0,
                                    "end_of_turn": True, "speaker_label": "A",
                                    "transcript": "[PERSON_NAME] takes metformin."})
                        await emit({"type": "SpeakerRevision", "revisions": [
                            {"turn_order": 0, "speaker_label": "B"}]})
                        await emit({"type": "Termination"})
                        return

            async with serve(handler, "127.0.0.1", 0) as server:
                port = server.sockets[0].getsockname()[1]
                call = transcribe(audio, output, "test-key", f"ws://127.0.0.1:{port}")
                if fail:
                    with self.assertRaises(TranscriptionError):
                        await call
                    self.assertFalse(output.exists())
                else:
                    await call
                    self.assertEqual(bytes(received), pcm)
                    self.assertGreaterEqual(times[-1], 0.225)
                    self.assertEqual(json.loads(output.read_text()), {"turns": [
                        {"speaker": "B", "text": "[PERSON_NAME] takes metformin."}]})
                self.assertEqual(Path(str(output) + ".frames.jsonl").read_text(),
                                 "".join(raw + "\n" for raw in messages))

    async def test_pacing_audit_and_revisions(self):
        await self.exercise()

    async def test_required_redaction_not_confirmed(self):
        await self.exercise(fail=True)


if __name__ == "__main__":
    unittest.main()
