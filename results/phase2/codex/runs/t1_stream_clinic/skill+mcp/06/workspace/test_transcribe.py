import asyncio
import io
import json
import time
import unittest
import wave

from transcribe import Turns, TranscriptionError, receive_frame, send_audio, validate_begin


class AggregationTests(unittest.TestCase):
    def test_final_text_and_incremental_revisions(self):
        turns = Turns()
        for order in (2, 1):
            turns.accept({"type": "Turn", "turn_order": order, "end_of_turn": True,
                          "speaker_label": "PENDING", "transcript": "[PERSON_NAME]."})
        turns.accept({"type": "Turn", "turn_order": 1, "end_of_turn": True,
                      "speaker_label": "A", "transcript": "Hello [PERSON_NAME]."})
        for speaker in ("B", "C"):
            turns.accept({"type": "SpeakerRevision", "revisions": [
                {"turn_order": 1, "speaker_label": speaker,
                 "words": [{"text": "Never use revision text"}]}]})
        self.assertEqual(turns.result(), {"turns": [
            {"speaker": "C", "text": "Hello [PERSON_NAME]."},
            {"speaker": "PENDING", "text": "[PERSON_NAME]."}]})

    def test_fail_if_required_features_not_confirmed(self):
        config = {"model": "universal-3-6-pro", "domain": "medical-v1",
                  "speaker_labels": True, "redact_pii": True}
        validate_begin({"type": "Begin", "configuration": config})
        for key in config:
            with self.subTest(key=key), self.assertRaises(TranscriptionError):
                validate_begin({"type": "Begin", "configuration": {**config, key: None}})

    def test_reject_partial(self):
        with self.assertRaises(TranscriptionError):
            Turns().accept({"type": "Turn", "end_of_turn": False})


class AsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_exact_audit_including_error(self):
        class Socket:
            async def recv(self):
                return '{ "type" : "Error", "error": "test", "extra": 1 }'

        audit = io.StringIO()
        with self.assertRaises(TranscriptionError):
            await receive_frame(Socket(), audit)
        self.assertEqual(audit.getvalue(),
                         '{ "type" : "Error", "error": "test", "extra": 1 }\n')

    async def test_pcm_pacing_and_short_last_chunk(self):
        source = io.BytesIO()
        with wave.open(source, "wb") as output:
            output.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            output.writeframes(b"\x01\x00" * 1700)
        source.seek(0)

        class Socket:
            def __init__(self):
                self.messages = []

            async def send(self, data):
                self.messages.append((time.monotonic(), data))

        socket = Socket()
        with wave.open(source, "rb") as audio:
            await send_audio(socket, audio)
        self.assertEqual(len(socket.messages), 4)
        chunks = [data for _, data in socket.messages[:3]]
        self.assertTrue(all(len(chunk) == 1600 for chunk in chunks))
        self.assertEqual(b"".join(chunks), b"\x01\x00" * 1700 + b"\0" * 1400)
        for previous, current in zip(socket.messages, socket.messages[1:]):
            self.assertGreaterEqual(current[0] - previous[0], 0.049)
        self.assertEqual(json.loads(socket.messages[-1][1]), {"type": "Terminate"})


if __name__ == "__main__":
    unittest.main()
