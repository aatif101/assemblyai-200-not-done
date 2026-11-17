import asyncio
import io
import json
import time
import unittest
from unittest.mock import AsyncMock
import wave

import transcribe as app


class TranscriptTests(unittest.TestCase):
    def test_replacements_order_and_revision_deltas(self):
        turns = app.Turns()
        for order in (2, 1):
            turns.accept({"type": "Turn", "turn_order": order, "end_of_turn": True,
                          "transcript": "[PERSON_NAME] takes metformin.", "speaker_label": "A"})
        turns.accept({"type": "Turn", "turn_order": 2, "end_of_turn": True,
                      "transcript": "Final text.", "speaker_label": "PENDING"})
        for label in ("B", "C"):
            turns.accept({"type": "SpeakerRevision", "revisions": [
                {"turn_order": 2, "speaker_label": label, "words": []}]})
        self.assertEqual(turns.result(), {"turns": [
            {"speaker": "A", "text": "[PERSON_NAME] takes metformin."},
            {"speaker": "C", "text": "Final text."}]})

    def test_reject_missing_or_disabled_configuration(self):
        config = {"model": app.MODEL, "domain": "medical-v1", "speaker_labels": True, "redact_pii": True}
        app.validate_begin({"type": "Begin", "configuration": config})
        for field in config:
            broken = dict(config)
            del broken[field]
            with self.assertRaises(app.TranscriptionError):
                app.validate_begin({"type": "Begin", "configuration": broken})

    def test_partials_and_errors_fail(self):
        for message in ({"type": "Turn", "end_of_turn": False}, {"type": "Error"}):
            with self.assertRaises(app.TranscriptionError):
                app.Turns().accept(message)


class StreamingTests(unittest.IsolatedAsyncioTestCase):
    async def test_audit_preserves_payload_and_order(self):
        payloads = ['{ "type" : "Heartbeat", "extra": 42 }',
                    '{"type":"Termination","audio_duration_seconds":0}']
        ws = AsyncMock()
        ws.recv.side_effect = payloads
        audit = io.BytesIO()
        await app.collect(ws, audit, app.Turns())
        self.assertEqual(audit.getvalue(), ('\n'.join(payloads) + '\n').encode())

    async def test_audio_pacing_padding_and_termination(self):
        audio = io.BytesIO()
        pcm = b'\x01\x02' * 1700
        with wave.open(audio, 'wb') as w:
            w.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
            w.writeframes(pcm)
        audio.seek(0)
        sent = []
        ws = AsyncMock()

        async def send(data):
            sent.append((time.monotonic(), data))

        ws.send.side_effect = send
        start = time.monotonic()
        with wave.open(audio) as source:
            await app.send_audio(ws, source)
        chunks = [data for _, data in sent[:-1]]
        self.assertEqual([len(c) for c in chunks], [1600, 1600, 1600])
        self.assertEqual(b''.join(chunks), pcm + b'\0' * 1400)
        self.assertGreaterEqual(sent[0][0] - start, 0.049)
        self.assertGreaterEqual(sent[2][0] - start, 0.149)
        self.assertEqual(json.loads(sent[-1][1]), {"type": "Terminate"})


if __name__ == '__main__':
    unittest.main()
