import asyncio
import io
import json
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
import wave

from websockets.asyncio.server import serve

import transcribe as app


def turn(order, text, speaker="A", formatted=True, final=True, words=None):
    return {"type": "Turn", "turn_order": order, "transcript": text,
            "speaker_label": speaker, "turn_is_formatted": formatted,
            "end_of_turn": final, "words": words or []}


class TurnTests(unittest.TestCase):
    def test_final_updates_replace_in_order_and_ignore_partials(self):
        turns = app.Turns()
        turns.accept(turn(1, "second"))
        turns.accept(turn(0, "provisional", final=False))
        turns.accept(turn(0, "first draft", formatted=False))
        turns.accept(turn(0, "First final."))
        turns.accept(turn(0, "late draft", formatted=False))
        self.assertEqual(turns.result(), {"turns": [
            {"speaker": "A", "text": "First final."},
            {"speaker": "A", "text": "second"}]})

    def test_split_mixed_speakers_without_losing_redactions_or_punctuation(self):
        turns = app.Turns()
        turns.accept(turn(0, "Hello, [PERSON_NAME]. Yes?", words=[
            {"text": "Hello,", "speaker": "A"},
            {"text": "[PERSON_NAME].", "speaker": "A"},
            {"text": "Yes?", "speaker": "B"}]))
        self.assertEqual(turns.result()["turns"], [
            {"speaker": "A", "text": "Hello, [PERSON_NAME]."},
            {"speaker": "B", "text": "Yes?"}])

    def test_speaker_revision_cannot_replace_redacted_text(self):
        turns = app.Turns()
        turns.accept(turn(0, "[PERSON_NAME]", words=[
            {"text": "[PERSON_NAME]", "start": 0, "end": 100, "speaker": "A"}]))
        turns.accept({"type": "SpeakerRevision", "revisions": [
            {"turn_order": 0, "speaker_label": "B", "words": [
                {"start": 0, "end": 100, "speaker": "B", "text": "untrusted replacement"}]}]})
        self.assertEqual(turns.result()["turns"], [
            {"speaker": "B", "text": "[PERSON_NAME]"}])

    def test_privacy_and_medical_session_parameters(self):
        params = parse_qs(urlsplit(app.session_url()).query)
        for key in ("speaker_labels", "redact_pii"):
            self.assertEqual(params[key], ["true"])
        self.assertEqual(params["include_partial_turns"], ["false"])
        self.assertEqual(params["domain"], ["medical-v1"])
        self.assertEqual(params["mode"], ["max_accuracy"])
        policies = json.loads(params["redact_pii_policies"][0])
        self.assertTrue({"person_name", "phone_number", "us_social_security_number"} <= set(policies))
        self.assertTrue({"drug", "medical_condition", "medical_process"}.isdisjoint(policies))


class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    def audio(self):
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as audio:
            audio.setparams((1, 2, 16000, 4800, "NONE", "not compressed"))
            audio.writeframes(b"\0\0" * 4800)
        buffer.seek(0)
        return wave.open(buffer, "rb")

    async def test_pacing_raw_audit_and_drain_after_terminate(self):
        frames = [
            '{ "type": "Begin", "id": "test", "extra": 42 }',
            json.dumps(turn(0, "draft", formatted=False)),
            json.dumps(turn(0, "Masked [PHONE_NUMBER].", speaker="B")),
            '{"type":"Termination","extra_field":true}',
        ]
        chunks = []
        async def server(ws):
            await ws.send(frames[0])
            start = asyncio.get_running_loop().time()
            async for message in ws:
                if isinstance(message, bytes):
                    chunks.append((asyncio.get_running_loop().time() - start, message))
                    if len(chunks) == 1:
                        await ws.send(frames[1])
                else:
                    self.assertEqual(json.loads(message), {"type": "Terminate"})
                    await ws.send(frames[2])
                    await ws.send(frames[3])
                    return
        async with serve(server, "127.0.0.1", 0) as service:
            url = f"ws://127.0.0.1:{service.sockets[0].getsockname()[1]}"
            audit = io.StringIO()
            with self.audio() as audio, patch.object(app, "session_url", return_value=url):
                result = await app.stream(audio, audit, "test-key")
        self.assertEqual(audit.getvalue(), "\n".join(frames) + "\n")
        self.assertEqual(result, {"turns": [
            {"speaker": "B", "text": "Masked [PHONE_NUMBER]."}]})
        self.assertEqual(b"".join(chunk for _, chunk in chunks), b"\0\0" * 4800)
        self.assertEqual(len(chunks), 3)
        for index, (elapsed, _) in enumerate(chunks, 1):
            self.assertGreaterEqual(elapsed, index * 0.1 - 0.02)

    async def test_provider_error_is_logged_and_fails(self):
        raw = '{"type":"Error","error":"test failure","new_field":1}'
        async def server(ws):
            await ws.send(raw)
            await ws.wait_closed()
        async with serve(server, "127.0.0.1", 0) as service:
            url = f"ws://127.0.0.1:{service.sockets[0].getsockname()[1]}"
            audit = io.StringIO()
            with self.audio() as audio, patch.object(app, "session_url", return_value=url):
                with self.assertRaises(app.TranscriptionError):
                    await app.stream(audio, audit, "test-key")
            self.assertEqual(audit.getvalue(), raw + "\n")

    async def test_disconnect_without_termination_fails(self):
        async def server(ws):
            await ws.send('{"type":"Begin"}')
            async for message in ws:
                if isinstance(message, str):
                    return
        async with serve(server, "127.0.0.1", 0) as service:
            url = f"ws://127.0.0.1:{service.sockets[0].getsockname()[1]}"
            with self.audio() as audio, patch.object(app, "session_url", return_value=url):
                with self.assertRaises(Exception):
                    await app.stream(audio, io.StringIO(), "test-key")


if __name__ == "__main__":
    unittest.main()
