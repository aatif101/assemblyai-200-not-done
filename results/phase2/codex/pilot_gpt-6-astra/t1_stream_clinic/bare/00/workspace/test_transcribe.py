import asyncio
import io
import json
import unittest
from unittest.mock import patch
import wave

from transcribe import Turns, send_audio, transcribe, TranscriptionError


class Tests(unittest.TestCase):
    def test_final_replaces_partial_and_formatted_replaces_final(self):
        turns = Turns()
        for text, final, formatted in [('hel', False, False), ('hello', True, False),
                                        ('Hello.', True, True), ('hello', True, False)]:
            turns.receive(dict(type='Turn', turn_order=1, transcript=text,
                               end_of_turn=final, turn_is_formatted=formatted,
                               speaker_label='A'))
        turns.receive(dict(type='Turn', turn_order=0, transcript='[PERSON_NAME]',
                           end_of_turn=True, speaker_label='B'))
        turns.receive(dict(type='SpeakerRevision', revisions=[dict(turn_order=1, speaker_label='C')]))
        self.assertEqual(turns.result(), {'turns': [
            {'speaker': 'B', 'text': '[PERSON_NAME]'}, {'speaker': 'C', 'text': 'Hello.'}]})

    def test_unfinished_turn_fails(self):
        turns = Turns()
        turns.receive(dict(type='Turn', turn_order=0, transcript='unfinished', end_of_turn=False))
        with self.assertRaises(TranscriptionError):
            turns.result()

    def test_pacing_and_pcm_without_header(self):
        async def check():
            buf = io.BytesIO()
            with wave.open(buf, 'wb') as out:
                out.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
                out.writeframes(b'\x01\x02' * 3520)
            buf.seek(0)
            class Socket:
                def __init__(self):
                    self.sent = []
                async def send(self, data):
                    self.sent.append((asyncio.get_running_loop().time(), data))
            ws = Socket()
            start = asyncio.get_running_loop().time()
            with wave.open(buf, 'rb') as source:
                await send_audio(ws, source)
            self.assertGreaterEqual(ws.sent[-1][0] - start, 1.25)
            self.assertEqual(ws.sent[0][1], b'\x01\x02' * 1600)
            self.assertEqual(ws.sent[2][1], b'\x01\x02' * 320 + b'\x00' * 960)
            self.assertEqual(json.loads(ws.sent[-1][1]), {'type': 'Terminate'})
        asyncio.run(check())

    def test_raw_audit_and_no_audio_without_redaction_confirmation(self):
        raw = '{ "type" : "Begin", "configuration": {"redact_pii":false} }'
        class Socket:
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def recv(self): return raw
            async def send(self, data): raise AssertionError('Must not send audio')
        audit = io.StringIO()
        with patch('transcribe.connect', return_value=Socket()):
            with self.assertRaises(TranscriptionError):
                asyncio.run(transcribe(None, audit, 'test-key'))
        self.assertEqual(audit.getvalue(), raw + '\n')


if __name__ == '__main__':
    unittest.main()
