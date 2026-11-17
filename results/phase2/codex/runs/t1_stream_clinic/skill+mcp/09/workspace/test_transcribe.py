import asyncio
import io
import json
import unittest
import wave

from transcribe import (
    MODEL, Transcript, TranscriptionError, receive_message, send_audio, validate_begin,
)


class TranscriptTests(unittest.TestCase):
    def test_finals_replace_and_revisions_apply_in_order(self):
        transcript = Transcript()
        for order, text in [(2, "second"), (0, "first"), (2, "second.")]:
            transcript.accept({"type": "Turn", "end_of_turn": True,
                               "turn_order": order, "transcript": text, "speaker_label": "A"})
        for label in ["B", "C"]:
            transcript.accept({"type": "SpeakerRevision", "revisions": [
                {"turn_order": 0, "speaker_label": label,
                 "words": [{"text": "must not replace redacted text"}]}]})
        self.assertEqual(transcript.result(), {"turns": [
            {"speaker": "C", "text": "first"}, {"speaker": "A", "text": "second."}]})

    def test_partials_and_server_errors_fail(self):
        for message in [{"type": "Turn", "end_of_turn": False}, {"type": "Error"}]:
            with self.assertRaises(TranscriptionError):
                Transcript().accept(message)

    def test_required_settings_confirmed_before_audio(self):
        config = {"model": MODEL, "domain": "medical-v1",
                  "redact_pii": True, "speaker_labels": True}
        validate_begin({"type": "Begin", "configuration": config})
        for key in config:
            with self.assertRaises(TranscriptionError):
                validate_begin({"type": "Begin", "configuration": {**config, key: None}})


class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_audit_preserves_payloads_and_arrival_order(self):
        frames = ['{ "type": "Begin", "extra": 7 }', '{"type":"Termination"}']
        class Socket:
            async def recv(self):
                return frames.pop(0)
        audit = io.BytesIO()
        expected = "\n".join(frames) + "\n"
        for _ in range(2):
            await receive_message(Socket(), audit)
        self.assertEqual(audit.getvalue(), expected.encode())

    async def test_audio_is_pcm_paced_and_last_chunk_is_padded(self):
        source = io.BytesIO()
        with wave.open(source, "wb") as writer:
            writer.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            writer.writeframes(b"\x01\x02" * 1900)
        source.seek(0)
        sent = []
        loop = asyncio.get_running_loop()
        class Socket:
            async def send(self, frame):
                sent.append((loop.time(), frame))
        start = loop.time()
        with wave.open(source, "rb") as audio:
            await send_audio(Socket(), audio)
        self.assertEqual([len(frame) for _, frame in sent[:-1]], [1600, 1600, 1600])
        self.assertEqual(sent[0][1][:4], b"\x01\x02\x01\x02")
        self.assertEqual(sent[2][1], b"\x01\x02" * 300 + b"\0" * 1000)
        self.assertGreaterEqual(sent[2][0] - start, 0.15)
        self.assertEqual(json.loads(sent[-1][1]), {"type": "Terminate"})


if __name__ == "__main__":
    unittest.main()
