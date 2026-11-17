import asyncio
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
import wave

from websockets.asyncio.server import serve
import transcribe


class StreamingTest(unittest.IsolatedAsyncioTestCase):
    async def test_wire_audit_pacing_and_final_revisions(self):
        messages = [
            json.dumps({"type": "Begin", "configuration": {
                "model": transcribe.MODEL, "domain": "medical-v1",
                "speaker_labels": True, "redact_pii": True}}),
            '{ "type": "FutureEvent", "new_field": 42 }',
            json.dumps({"type": "Turn", "turn_order": 1, "transcript": "Hello [PERSON_NAME].",
                        "end_of_turn": True, "speaker_label": "PENDING"}),
            json.dumps({"type": "Turn", "turn_order": 1, "transcript": "Hello, [PERSON_NAME].",
                        "end_of_turn": True, "speaker_label": "A"}),
            json.dumps({"type": "SpeakerRevision", "revisions": [
                {"turn_order": 1, "speaker_label": "B"}]}),
            json.dumps({"type": "Termination"}),
        ]
        packets = []
        async def handler(ws):
            params = parse_qs(urlsplit(ws.request.path).query)
            self.assertEqual(params["include_partial_turns"], ["false"])
            self.assertEqual(params["redact_pii"], ["true"])
            self.assertNotIn("redact_pii_policies", params)
            await ws.send(messages[0])
            await ws.send(messages[1])
            async for data in ws:
                if isinstance(data, bytes):
                    packets.append((time.monotonic(), data))
                    if len(packets) == 1:
                        await ws.send(messages[2])
                else:
                    self.assertEqual(json.loads(data), {"type": "Terminate"})
                    for message in messages[3:]:
                        await ws.send(message)
                    break

        with tempfile.TemporaryDirectory() as directory:
            audio, output = Path(directory) / "input.wav", Path(directory) / "out.json"
            with wave.open(str(audio), "wb") as stream:
                stream.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                stream.writeframes(b"\1\0" * 1700)
            async with serve(handler, "127.0.0.1", 0) as server:
                port = server.sockets[0].getsockname()[1]
                with patch.object(transcribe, "ENDPOINT", f"ws://127.0.0.1:{port}/v3/ws"), patch.dict(
                    "os.environ", {"ASSEMBLYAI_API_KEY": "test-key"}
                ):
                    await transcribe.run(audio, output)
            self.assertEqual(json.loads(output.read_text()), {"turns": [
                {"speaker": "B", "text": "Hello, [PERSON_NAME]."}]})
            self.assertEqual(Path(str(output) + ".frames.jsonl").read_text(), "\n".join(messages) + "\n")
            self.assertEqual(len(packets), 3)
            self.assertTrue(all(len(packet) == 1600 for _, packet in packets))
            self.assertGreaterEqual(packets[-1][0] - packets[0][0], 0.095)
            self.assertEqual(packets[0][1][:4], b"\1\0\1\0")

    def test_configuration_must_confirm_redaction(self):
        with self.assertRaises(RuntimeError):
            transcribe.validate_begin({"configuration": {"model": transcribe.MODEL}})

    def test_partials_are_rejected(self):
        with self.assertRaises(RuntimeError):
            transcribe.Turns().accept({"type": "Turn", "end_of_turn": False})

    async def test_disconnect_keeps_audit_without_publishing_success(self):
        begin = json.dumps({"type": "Begin", "configuration": {
            "model": transcribe.MODEL, "domain": "medical-v1",
            "speaker_labels": True, "redact_pii": True}})

        async def handler(ws):
            await ws.send(begin)
            await ws.close()

        with tempfile.TemporaryDirectory() as directory:
            audio, output = Path(directory) / "input.wav", Path(directory) / "out.json"
            with wave.open(str(audio), "wb") as stream:
                stream.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                stream.writeframes(b"\0\0" * 800)
            async with serve(handler, "127.0.0.1", 0) as server:
                port = server.sockets[0].getsockname()[1]
                with patch.object(transcribe, "ENDPOINT", f"ws://127.0.0.1:{port}/v3/ws"), patch.dict(
                    "os.environ", {"ASSEMBLYAI_API_KEY": "test-key"}
                ):
                    with self.assertRaises(Exception):
                        await transcribe.run(audio, output)
            self.assertFalse(output.exists())
            self.assertEqual(Path(str(output) + ".frames.jsonl").read_text(), begin + "\n")


if __name__ == "__main__":
    unittest.main()
