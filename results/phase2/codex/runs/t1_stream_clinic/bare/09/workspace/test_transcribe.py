import asyncio
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import wave

from websockets.asyncio.client import connect as real_connect
from websockets.asyncio.server import serve

import transcribe as app


def turn(order, text, speaker="A", **extra):
    return {"type": "Turn", "turn_order": order, "transcript": text,
            "end_of_turn": True, "turn_is_formatted": True,
            "speaker_label": speaker, "words": [], **extra}


class AggregationTests(unittest.TestCase):
    def test_final_updates_order_and_no_partial_duplicates(self):
        c = app.Turns()
        c.accept(turn(1, "second"))
        c.accept(turn(0, "part", end_of_turn=False, turn_is_formatted=False))
        c.accept(turn(0, "first", turn_is_formatted=False))
        c.accept(turn(0, "First."))
        c.accept(turn(0, "first", turn_is_formatted=False))
        self.assertEqual(c.result(), {"turns": [
            {"speaker": "A", "text": "First."}, {"speaker": "A", "text": "second"}]})

    def test_word_speakers_and_revisions_preserve_redacted_text(self):
        c = app.Turns()
        c.accept(turn(0, "Hello [PERSON_NAME]. Yes?", words=[
            {"text": "Hello", "speaker": "A", "start": 0, "end": 10},
            {"text": "[PERSON_NAME].", "speaker": "A", "start": 11, "end": 20},
            {"text": "Yes?", "speaker": "A", "start": 21, "end": 30},
        ]))
        c.accept({"type": "SpeakerRevision", "revisions": [{"turn_order": 0,
            "words": [{"text": "not-used", "speaker": "B", "start": 21, "end": 30}]}]})
        self.assertEqual(c.result()["turns"], [
            {"speaker": "A", "text": "Hello [PERSON_NAME]."},
            {"speaker": "B", "text": "Yes?"}])

    def test_unfinished_turn_fails(self):
        c = app.Turns()
        c.accept(turn(0, "unfinished", end_of_turn=False))
        with self.assertRaises(app.TranscriptionError):
            c.result()


class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self, *, redaction=True, api_error=False):
        sent = []
        audio_packets = []
        received_at = []

        async def server(ws):
            async def emit(raw):
                sent.append(raw)
                await ws.send(raw)
            await emit(json.dumps({"type": "Begin", "configuration": {
                "redact_pii": redaction, "speaker_labels": True, "domain": "medical-v1"}}, indent=None))
            if not redaction:
                await ws.wait_closed()
                return
            async for packet in ws:
                if isinstance(packet, bytes):
                    audio_packets.append(packet)
                    received_at.append(time.monotonic())
                    if api_error:
                        await emit('{ "type": "Error", "error": "test failure" }')
                        return
                elif json.loads(packet)["type"] == "Terminate":
                    await emit(json.dumps(turn(0, "Hello [PERSON_NAME].")))
                    await emit('{ "type": "Termination" }')
                    return

        with tempfile.TemporaryDirectory() as tmp:
            wav_path = Path(tmp) / "input.wav"
            pcm = b"\x11\x00" * 3200
            with wave.open(str(wav_path), "wb") as wav:
                wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                wav.writeframes(pcm)
            async with serve(server, "127.0.0.1", 0) as ws_server:
                port = ws_server.sockets[0].getsockname()[1]
                def redirect(url, **kwargs):
                    self.assertIn("redact_pii=true", url)
                    self.assertIn("include_partial_turns=false", url)
                    self.assertIn("domain=medical-v1", url)
                    return real_connect(f"ws://127.0.0.1:{port}", **kwargs)
                audit = io.StringIO()
                with wave.open(str(wav_path), "rb") as audio, patch.object(app, "connect", redirect):
                    if not redaction or api_error:
                        with self.assertRaises(app.TranscriptionError):
                            await app.transcribe(audio, audit, "test-key")
                    else:
                        result = await app.transcribe(audio, audit, "test-key")
                        self.assertEqual(result, {"turns": [
                            {"speaker": "A", "text": "Hello [PERSON_NAME]."}]})
                self.assertEqual(audit.getvalue(), "".join(raw + "\n" for raw in sent))
        return pcm, audio_packets, received_at

    async def test_paced_pcm_exact_audit_and_final_result(self):
        pcm, packets, times = await self.exercise()
        self.assertEqual(b"".join(packets), pcm)
        self.assertEqual(len(packets), 2)
        self.assertGreaterEqual(times[1] - times[0], 0.075)

    async def test_no_audio_without_redaction_confirmation(self):
        _, packets, _ = await self.exercise(redaction=False)
        self.assertEqual(packets, [])

    async def test_api_error_fails_and_is_audited_verbatim(self):
        await self.exercise(api_error=True)


if __name__ == "__main__":
    unittest.main()
