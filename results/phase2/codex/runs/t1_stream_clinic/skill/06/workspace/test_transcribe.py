"""Local protocol tests; no credentials or clinical recordings required."""
import asyncio
import io
import json
import time
import unittest
import wave

from websockets.asyncio.server import serve
from transcribe import MODEL, TranscriptionError, stream


class StreamingTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self, config_override=None, terminate=True):
        frames = []
        audio = []
        times = []
        config = {"model": MODEL, "domain": "medical-v1", "speaker_labels": True, "redact_pii": True}
        config.update(config_override or {})

        async def handler(ws):
            self.assertEqual(ws.request.headers['Authorization'], 'test-key')
            self.assertIn('include_partial_turns=false', ws.request.path)
            self.assertIn('redact_pii=true', ws.request.path)

            async def emit(event):
                raw = json.dumps(event, separators=(', ', ': '))
                frames.append(raw)
                await ws.send(raw)

            await emit({"type": "Begin", "configuration": config})
            try:
                async for message in ws:
                    if isinstance(message, bytes):
                        audio.append(message)
                        times.append(time.monotonic())
                        if len(audio) == 1:
                            await emit({"type": "Turn", "turn_order": 1, "end_of_turn": True,
                                        "speaker_label": "A", "transcript": "[PERSON_NAME] takes metformin."})
                    else:
                        self.assertEqual(json.loads(message), {"type": "Terminate"})
                        await emit({"type": "Turn", "turn_order": 0, "end_of_turn": True,
                                    "speaker_label": "A", "transcript": "A superseded version."})
                        await emit({"type": "Turn", "turn_order": 0, "end_of_turn": True,
                                    "speaker_label": "A", "transcript": "What medications do you take?"})
                        await emit({"type": "SpeakerRevision", "revisions": [{"turn_order": 1, "speaker_label": "C"}]})
                        await emit({"type": "SpeakerRevision", "revisions": [{"turn_order": 1, "speaker_label": "B"}]})
                        if terminate:
                            await emit({"type": "Termination"})
                        return
            except Exception:
                if not config_override:
                    raise

        source = io.BytesIO()
        with wave.open(source, 'wb') as wav:
            wav.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
            wav.writeframes(b'\x01\x00' * 1700)  # 106.25ms: test a short final chunk.
        source.seek(0)
        audit = io.StringIO()
        async with serve(handler, '127.0.0.1', 0) as server:
            port = server.sockets[0].getsockname()[1]
            with wave.open(source, 'rb') as wav:
                try:
                    result = await stream(wav, audit, 'test-key', f'ws://127.0.0.1:{port}')
                except Exception:
                    self.assertEqual(audit.getvalue(), ''.join(frame + '\n' for frame in frames))
                    if config_override:
                        self.assertEqual(audio, [])
                    raise
        self.assertEqual(audit.getvalue(), ''.join(frame + '\n' for frame in frames))
        self.assertEqual([len(chunk) for chunk in audio], [1600, 1600, 1600])
        self.assertEqual(b''.join(audio)[:3400], b'\x01\x00' * 1700)
        self.assertTrue(all(b - a >= 0.045 for a, b in zip(times, times[1:])))
        return result

    async def test_frames_pacing_final_turns_and_revisions(self):
        self.assertEqual(await self.exercise(), {"turns": [
            {"speaker": "A", "text": "What medications do you take?"},
            {"speaker": "B", "text": "[PERSON_NAME] takes metformin."}]})

    async def test_refuse_audio_if_redaction_not_confirmed(self):
        with self.assertRaises(TranscriptionError):
            await self.exercise({"redact_pii": False})

    async def test_close_without_termination_fails(self):
        with self.assertRaises(Exception):
            await self.exercise(terminate=False)


if __name__ == '__main__':
    unittest.main()
