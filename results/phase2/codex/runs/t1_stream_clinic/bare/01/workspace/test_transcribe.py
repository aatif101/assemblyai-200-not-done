import asyncio
import io
import json
import os
import subprocess
import time
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
import wave

from websockets.asyncio.client import connect
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

import transcribe


def wav(samples=4000):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setparams((1, 2, 16000, samples, "NONE", "not compressed"))
        writer.writeframes(b"\x01\x00" * samples)
    buffer.seek(0)
    return wave.open(buffer, "rb")


BEGIN = {"type": "Begin", "configuration": {
    "redact_pii": True, "speaker_labels": True, "domain": "medical-v1"}}


class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_pacing_raw_audit_final_updates_and_revisions(self):
        messages = [json.dumps(BEGIN), '{"type": "FutureEvent", "extra": [1, 2]}',
                    json.dumps({"type": "Turn", "turn_order": 0,
                                "end_of_turn": False, "transcript": "Partial"}),
                    json.dumps({"type": "Turn", "turn_order": 0,
                                "end_of_turn": True, "turn_is_formatted": False,
                                "transcript": "hello [PERSON_NAME]", "speaker_label": "A"}),
                    json.dumps({"type": "Turn", "turn_order": 0,
                                "end_of_turn": True, "turn_is_formatted": True,
                                "transcript": "Hello, [PERSON_NAME].", "speaker_label": "A"}),
                    json.dumps({"type": "SpeakerRevision", "revisions": [
                        {"turn_order": 0, "speaker_label": "B"}]}),
                    '{"type":"Termination","extra":"preserve me"}']
        chunks = []
        elapsed = []

        async def handler(ws):
            started = time.monotonic()
            await ws.send(messages[0])
            await ws.send(messages[1])
            async for raw in ws:
                if isinstance(raw, bytes):
                    chunks.append(raw)
                elif json.loads(raw)["type"] == "Terminate":
                    elapsed.append(time.monotonic() - started)
                    for message in messages[2:]:
                        await ws.send(message)
                    return

        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]

            def local_connect(uri, **kwargs):
                params = parse_qs(urlsplit(uri).query)
                self.assertEqual(params["domain"], ["medical-v1"])
                self.assertEqual(params["redact_pii"], ["true"])
                self.assertIn("us_social_security_number",
                              json.loads(params["redact_pii_policies"][0]))
                self.assertEqual(params["include_partial_turns"], ["false"])
                return connect(f"ws://127.0.0.1:{port}", **kwargs)

            audit = io.StringIO()
            with patch.object(transcribe, "connect", local_connect), wav() as audio:
                result = await transcribe.run_session(audio, audit, "test-key")
        self.assertEqual(audit.getvalue(), "".join(m + "\n" for m in messages))
        self.assertEqual(result, {"turns": [{"speaker": "B", "text": "Hello, [PERSON_NAME]."}]})
        self.assertEqual(b"".join(chunks), b"\x01\x00" * 4000)
        self.assertEqual([len(c) for c in chunks], [3200, 3200, 1600])
        self.assertGreaterEqual(elapsed[0], 0.24)

    async def test_premature_disconnect_is_failure_and_audit_survives(self):
        message = json.dumps(BEGIN)

        async def handler(ws):
            await ws.send(message)
            await ws.close()

        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            audit = io.StringIO()
            with patch.object(transcribe, "connect", lambda uri, **kw: connect(
                    f"ws://127.0.0.1:{port}", **kw)), wav() as audio:
                with self.assertRaises(ExceptionGroup):
                    await asyncio.wait_for(transcribe.run_session(audio, audit, "test-key"), 2)
            self.assertEqual(audit.getvalue(), message + "\n")

    async def test_missing_privacy_confirmation_sends_no_audio(self):
        received = []

        async def handler(ws):
            await ws.send('{"type":"Begin","configuration":{"redact_pii":false}}')
            try:
                async for raw in ws:
                    received.append(raw)
            except ConnectionClosed:
                pass

        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            with patch.object(transcribe, "connect", lambda uri, **kw: connect(
                    f"ws://127.0.0.1:{port}", **kw)), wav() as audio:
                with self.assertRaises(ExceptionGroup):
                    await transcribe.run_session(audio, io.StringIO(), "test-key")
        self.assertEqual(received, [])


class CliTests(unittest.TestCase):
    def test_missing_key_exits_nonzero(self):
        env = dict(os.environ)
        env.pop("ASSEMBLYAI_API_KEY", None)
        result = subprocess.run(["./run.sh", "sample.wav", "/tmp/unused-clinic.json"],
                                env=env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ASSEMBLYAI_API_KEY is required", result.stderr)


if __name__ == "__main__":
    unittest.main()
