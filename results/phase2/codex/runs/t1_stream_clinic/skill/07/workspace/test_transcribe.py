import asyncio
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import wave

import transcribe as app


class FakeSocket:
    def __init__(self, messages):
        self.messages = iter(messages)
        self.sent = []

    async def recv(self):
        return next(self.messages)

    async def send(self, data):
        self.sent.append((time.monotonic(), data))


class StreamingTests(unittest.IsolatedAsyncioTestCase):
    async def test_replacements_revisions_and_exact_audit(self):
        messages = [
            '{ "type":"Turn", "turn_order":2, "end_of_turn":true, "speaker_label":"B", "transcript":"Later." }',
            json.dumps(dict(type="Turn", turn_order=1, end_of_turn=True,
                            speaker_label="PENDING", transcript="[PERSON_NAME].")),
            json.dumps(dict(type="Turn", turn_order=1, end_of_turn=True,
                            speaker_label="A", transcript="Hello [PERSON_NAME].")),
            json.dumps(dict(type="SpeakerRevision", revisions=[dict(turn_order=1, speaker_label="B")])),
            json.dumps(dict(type="SpeakerRevision", revisions=[dict(turn_order=1, speaker_label="A")])),
            '{"type":"Termination"}',
        ]
        audit = io.BytesIO()
        result = await app.receive_turns(FakeSocket(messages), audit)
        self.assertEqual(result, {"turns": [
            {"speaker": "A", "text": "Hello [PERSON_NAME]."},
            {"speaker": "B", "text": "Later."}]})
        self.assertEqual(audit.getvalue(), ("\n".join(messages) + "\n").encode())

    async def test_pcm_only_paced_and_final_chunk_padded(self):
        data = io.BytesIO()
        with wave.open(data, "wb") as wav:
            wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            wav.writeframes(b"\x01\x02" * 901)
        data.seek(0)
        socket = FakeSocket([])
        start = time.monotonic()
        with wave.open(data, "rb") as wav:
            await app.send_audio(socket, wav)
        self.assertGreaterEqual(socket.sent[0][0] - start, 0.049)
        self.assertGreaterEqual(socket.sent[1][0] - socket.sent[0][0], 0.049)
        self.assertEqual(socket.sent[0][1], b"\x01\x02" * 800)
        self.assertEqual(socket.sent[1][1], b"\x01\x02" * 101 + b"\0" * 1398)
        self.assertEqual(json.loads(socket.sent[-1][1]), {"type": "Terminate"})

    async def test_no_audio_when_redaction_not_confirmed(self):
        socket = FakeSocket([json.dumps(dict(type="Begin", configuration=dict(
            model=app.MODEL, domain="medical-v1", speaker_labels=True, redact_pii=False)))])

        class Connection:
            async def __aenter__(self):
                return socket

            async def __aexit__(self, *args):
                pass

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.wav"
            output = Path(directory) / "output.json"
            with wave.open(str(source), "wb") as wav:
                wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                wav.writeframes(b"\0" * 1600)
            with patch.object(app, "connect", return_value=Connection()), patch.dict(app.os.environ, {"ASSEMBLYAI_API_KEY": "test"}):
                with self.assertRaises(app.TranscriptionError):
                    await app.transcribe(source, output)
            self.assertFalse(socket.sent)
            self.assertFalse(output.exists())
            self.assertEqual(len(Path(str(output) + ".frames.jsonl").read_text().splitlines()), 1)

    async def test_error_and_partial_are_failures(self):
        for message in ({"type": "Error", "error": "test"},
                        {"type": "Turn", "end_of_turn": False}):
            audit = io.BytesIO()
            with self.assertRaises(app.TranscriptionError):
                await app.receive_turns(FakeSocket([json.dumps(message)]), audit)
            self.assertEqual(json.loads(audit.getvalue()), message)


if __name__ == "__main__":
    unittest.main()
