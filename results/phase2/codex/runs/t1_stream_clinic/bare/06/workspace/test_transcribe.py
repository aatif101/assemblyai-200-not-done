import asyncio
import io
import json
import time
import unittest
import wave

from transcribe import StreamFailure, Turns, receive, send_audio


def turn(order=0, text="Hello.", speaker="A", final=True, formatted=True):
    return {"type": "Turn", "turn_order": order, "transcript": text,
            "speaker_label": speaker, "end_of_turn": final,
            "turn_is_formatted": formatted, "words": []}


class TurnsTest(unittest.TestCase):
    def test_final_updates_order_and_speaker_revisions(self):
        turns = Turns()
        turns.receive(turn(2, "later", formatted=False))
        turns.receive(turn(0, "hel", final=False, formatted=False))
        turns.receive(turn(0, "hello", formatted=False))
        turns.receive(turn(0, "Hello."))
        turns.receive(turn(0, "hello", formatted=False))
        turns.receive({"type": "SpeakerRevision", "revisions": [
            {"turn_order": 0, "speaker_label": "B"}]})
        self.assertEqual(turns.result(), {"turns": [
            {"speaker": "B", "text": "Hello."}, {"speaker": "A", "text": "later"}]})

    def test_incomplete_turn_fails(self):
        turns = Turns()
        turns.receive(turn(final=False))
        with self.assertRaises(StreamFailure):
            turns.result()

    def test_word_speakers_and_revision(self):
        turns = Turns()
        event = turn(text="Hello. Yes.")
        event["words"] = [{"text": "Hello.", "speaker": "A"},
                          {"text": "Yes.", "speaker": "A"}]
        turns.receive(event)
        turns.receive({"type": "SpeakerRevision", "revisions": [
            {"turn_order": 0, "speaker_label": "A", "words": [
                {"text": "Hello.", "speaker": "A"}, {"text": "Yes.", "speaker": "B"}]}]})
        self.assertEqual(turns.result()["turns"], [
            {"speaker": "A", "text": "Hello."}, {"speaker": "B", "text": "Yes."}])


class FakeSocket:
    def __init__(self, messages):
        self.messages = messages

    async def __aiter__(self):
        for message in self.messages:
            yield message


class ProtocolTest(unittest.IsolatedAsyncioTestCase):
    async def test_raw_payload_is_retained_without_reserialization(self):
        messages = [
            '{ "type" : "Begin", "configuration": {"redact_pii":true, '
            '"speaker_labels":true,"domain":"medical-v1"}, "extra": 123 }',
            json.dumps(turn(text="[PERSON_NAME] called.")),
            '{ "type":"Termination", "unknown_field":true }',
        ]
        log = io.BytesIO()
        turns = Turns()
        began = asyncio.Event()
        await receive(FakeSocket(messages), log, turns, began)
        self.assertEqual(log.getvalue(), ("\n".join(messages) + "\n").encode())
        self.assertTrue(began.is_set())
        self.assertEqual(len(turns.result()["turns"]), 1)

    async def test_error_is_logged_before_failure(self):
        message = '{"type":"Error","error":"test"}'
        log = io.BytesIO()
        with self.assertRaises(StreamFailure):
            await receive(FakeSocket([message]), log, Turns(), asyncio.Event())
        self.assertEqual(log.getvalue(), message.encode() + b"\n")

    async def test_requires_confirmation_before_audio(self):
        with self.assertRaises(StreamFailure):
            await receive(FakeSocket(['{"type":"Begin","configuration":{}}']),
                          io.BytesIO(), Turns(), asyncio.Event())

    async def test_disconnect_is_not_success(self):
        with self.assertRaises(StreamFailure):
            await receive(FakeSocket([]), io.BytesIO(), Turns(), asyncio.Event())

    async def test_pcm_chunks_are_paced_and_tail_padded(self):
        recording = io.BytesIO()
        with wave.open(recording, "wb") as audio:
            audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            audio.writeframes(b"\x01\x00" * 1700)
        recording.seek(0)
        sent = []

        class Sender:
            async def send(self, payload):
                sent.append((time.monotonic(), payload))

        started = time.monotonic()
        with wave.open(recording, "rb") as audio:
            await send_audio(Sender(), audio)
        chunks = [(when, data) for when, data in sent if isinstance(data, bytes)]
        self.assertEqual(len(chunks), 12)  # two source chunks, ten silence chunks
        self.assertTrue(all(len(data) == 3200 for _, data in chunks))
        self.assertGreaterEqual(chunks[0][0] - started, 0.09)
        self.assertTrue(all(b[0] - a[0] >= 0.09 for a, b in zip(chunks, chunks[1:])))
        self.assertEqual(chunks[1][1], b"\x01\x00" * 100 + b"\0" * 3000)
        self.assertEqual(json.loads(sent[-1][1]), {"type": "Terminate"})


if __name__ == "__main__":
    unittest.main()
