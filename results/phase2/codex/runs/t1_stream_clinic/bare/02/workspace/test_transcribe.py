import asyncio
import io
import json
import time
import unittest
import wave
from unittest.mock import patch

import transcribe


def turn(order, text, formatted=True, speaker="A"):
    return {"type": "Turn", "turn_order": order, "transcript": text,
            "end_of_turn": True, "turn_is_formatted": formatted,
            "speaker_label": speaker}


class TurnsTests(unittest.TestCase):
    def test_updates_order_and_speaker_revision(self):
        turns = transcribe.Turns()
        turns.accept(turn(1, "Second."))
        turns.accept(turn(0, "first", False))
        turns.accept(turn(0, "First."))
        turns.accept(turn(0, "stale", False))
        turns.accept({**turn(2, "partial"), "end_of_turn": False})
        turns.accept({"type": "SpeakerRevision", "revisions": [
            {"turn_order": 0, "speaker_label": "B"}]})
        self.assertEqual(turns.result(), {"turns": [
            {"speaker": "B", "text": "First."},
            {"speaker": "A", "text": "Second."}]})


class FakeSocket:
    def __init__(self, privacy=True, fail=False):
        self.incoming = asyncio.Queue()
        self.messages = []
        self.sent = []
        self.fail = fail
        self.enqueue(json.dumps({"type": "Begin", "configuration": {
            "redact_pii": privacy, "speaker_labels": True, "domain": "medical-v1"}}))

    def enqueue(self, raw):
        self.messages.append(raw)
        self.incoming.put_nowait(raw)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def recv(self):
        return await self.incoming.get()

    async def send(self, data):
        self.sent.append((time.monotonic(), data))
        if self.fail:
            self.enqueue('{"type":"Error","error":"test failure"}')
            self.fail = False
        elif isinstance(data, str):
            # Whitespace and extra fields must survive in the audit exactly.
            self.enqueue(' { "type": "Turn", "turn_order": 0, "end_of_turn": true, '
                         '"turn_is_formatted": true, "speaker_label": "B", '
                         '"transcript": "Hello [PERSON_NAME].", "extra": 1 } ')
            self.enqueue('{"type":"Termination"}')


class StreamTests(unittest.IsolatedAsyncioTestCase):
    def audio(self):
        buf = io.BytesIO()
        with wave.open(buf, "wb") as out:
            out.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            out.writeframes(bytes(3200))
        buf.seek(0)
        return wave.open(buf, "rb")

    async def test_raw_audit_pacing_and_final_turn_before_termination(self):
        ws = FakeSocket()
        audit = io.StringIO()
        with self.audio() as audio, patch.object(transcribe, "connect", return_value=ws):
            result = await transcribe.stream(audio, audit, "test-key")
        self.assertEqual(audit.getvalue(), "".join(m + "\n" for m in ws.messages))
        self.assertEqual(result, {"turns": [{"speaker": "B", "text": "Hello [PERSON_NAME]."}]})
        packets = [(t, d) for t, d in ws.sent if isinstance(d, bytes)]
        for (t, data), (next_t, _) in zip(packets, packets[1:]):
            self.assertGreaterEqual(next_t - t, len(data) / 32000 - .015)

    async def test_unconfirmed_privacy_sends_no_audio(self):
        ws = FakeSocket(privacy=False)
        with self.audio() as audio, patch.object(transcribe, "connect", return_value=ws):
            with self.assertRaises(transcribe.TranscriptionError):
                await transcribe.stream(audio, io.StringIO(), "test-key")
        self.assertEqual(ws.sent, [])

    async def test_server_error_fails_and_is_audited(self):
        ws = FakeSocket(fail=True)
        audit = io.StringIO()
        with self.audio() as audio, patch.object(transcribe, "connect", return_value=ws):
            with self.assertRaises(transcribe.TranscriptionError):
                await transcribe.stream(audio, audit, "test-key")
        self.assertIn('"type":"Error"', audit.getvalue())


if __name__ == "__main__":
    unittest.main()
