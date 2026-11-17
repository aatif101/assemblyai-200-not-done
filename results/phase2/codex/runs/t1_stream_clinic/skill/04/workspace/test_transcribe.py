import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import wave

import transcribe as app


class FakeSocket:
    def __init__(self, redact=True):
        self.incoming = asyncio.Queue()
        self.frames = []
        self.audio = []
        self.put(json.dumps({"type": "Begin", "configuration": {
            "model": app.MODEL, "domain": "medical-v1", "speaker_labels": True,
            "redact_pii": redact}}))

    def put(self, raw):
        self.frames.append(raw)
        self.incoming.put_nowait(raw)

    async def recv(self):
        return await self.incoming.get()

    async def send(self, message):
        if isinstance(message, bytes):
            self.audio.append(message)
            return
        assert json.loads(message) == {"type": "Terminate"}
        # Deliberately different JSON whitespace: the audit must preserve it.
        self.put('{ "type":"Turn", "turn_order":1, "end_of_turn":true,'
                 ' "speaker_label":"PENDING", "transcript":"[PERSON_NAME]." }')
        self.put(json.dumps({"type": "Turn", "turn_order": 2,
                            "end_of_turn": True, "speaker_label": "A",
                            "transcript": "Metformin."}))
        for label in ("A", "B"):
            self.put(json.dumps({"type": "SpeakerRevision", "revisions": [
                {"turn_order": 1, "speaker_label": label, "words": []}]}))
        self.put('{"type":"Termination"}')

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self, redact=True):
        socket = FakeSocket(redact)
        with tempfile.TemporaryDirectory() as folder:
            audio = Path(folder) / "input.wav"
            output = Path(folder) / "out.json"
            with wave.open(str(audio), "wb") as wav:
                wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                wav.writeframes(b"\x01\x00" * 1001)
            with patch.dict(os.environ, {"ASSEMBLYAI_API_KEY": "test-key"}), \
                    patch.object(app, "connect", return_value=socket) as connect:
                if not redact:
                    with self.assertRaises(app.SessionError):
                        await app.transcribe(audio, output)
                    self.assertEqual(socket.audio, [])
                    self.assertFalse(output.exists())
                    return
                start = asyncio.get_running_loop().time()
                await app.transcribe(audio, output)
                self.assertGreaterEqual(asyncio.get_running_loop().time() - start, .1)
                url = connect.call_args.args[0]
                self.assertIn("include_partial_turns=false", url)
                self.assertIn("redact_pii=true", url)
                self.assertEqual(json.loads(output.read_text()), {"turns": [
                    {"speaker": "B", "text": "[PERSON_NAME]."},
                    {"speaker": "A", "text": "Metformin."}]})
                self.assertEqual(Path(str(output) + ".frames.jsonl").read_bytes(),
                                 ("\n".join(socket.frames) + "\n").encode())
                self.assertEqual([len(chunk) for chunk in socket.audio], [1600, 1600])
                self.assertEqual(socket.audio[-1][-1198:], bytes(1198))

    async def test_replay_audit_and_last_revision(self):
        await self.exercise()

    async def test_refuse_audio_if_redaction_not_confirmed(self):
        await self.exercise(redact=False)


if __name__ == "__main__":
    unittest.main()
