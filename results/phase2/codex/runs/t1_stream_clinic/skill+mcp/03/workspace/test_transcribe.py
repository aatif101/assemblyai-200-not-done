import asyncio
import io
import json
import time
import unittest
from unittest.mock import patch

import transcribe as app


class FakeSocket:
    def __init__(self):
        self.queue = asyncio.Queue()
        self.raw = []
        self.audio = []
        self.times = []
        self.emit({"type": "Begin", "configuration": {
            "model": app.MODEL, "domain": "medical-v1",
            "speaker_labels": True, "redact_pii": True}})

    def emit(self, obj):
        raw = json.dumps(obj, separators=(", ", ": "))
        self.raw.append(raw)
        self.queue.put_nowait(raw)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def recv(self):
        return await self.queue.get()

    async def send(self, data):
        if isinstance(data, bytes):
            self.audio.append(data)
            self.times.append(time.monotonic())
            if len(self.audio) == 1:
                self.emit({"type": "Turn", "turn_order": 0,
                           "end_of_turn": True, "speaker_label": "PENDING",
                           "transcript": "Hello [PERSON_NAME]."})
        else:
            assert json.loads(data)["type"] == "Terminate"
            self.emit({"type": "SpeakerRevision", "revisions": [
                {"turn_order": 0, "speaker_label": "B", "words": []}]})
            self.emit({"type": "Termination"})


class PipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_raw_audit_pacing_tail_and_final_revision(self):
        socket = FakeSocket()
        audit = io.BytesIO()

        class Audio:
            chunks = iter([b"a" * 1600, b"b" * 100, b""])

            def readframes(self, count):
                return next(self.chunks)

        with patch.object(app, "connect", return_value=socket) as connector:
            result = await app.transcribe(Audio(), audit, "test-key")
        self.assertEqual(result, {"turns": [
            {"speaker": "B", "text": "Hello [PERSON_NAME]."}]})
        self.assertEqual(audit.getvalue(),
                         ("\n".join(socket.raw) + "\n").encode())
        self.assertEqual(socket.audio, [b"a" * 1600, b"b" * 100 + b"\0" * 1500])
        self.assertGreaterEqual(socket.times[1] - socket.times[0], 0.049)
        url = connector.call_args.args[0]
        self.assertIn("include_partial_turns=false", url)
        self.assertIn("redact_pii=true", url)

    def test_latest_final_and_revision_win_and_turns_sort(self):
        transcript = app.Transcript()
        for order in (2, 0, 2):
            transcript.accept({"type": "Turn", "turn_order": order,
                               "end_of_turn": True, "speaker_label": "A",
                               "transcript": str(order)})
        for label in ("B", "C"):
            transcript.accept({"type": "SpeakerRevision", "revisions": [
                {"turn_order": 0, "speaker_label": label}]})
        self.assertEqual(transcript.result(), {"turns": [
            {"speaker": "C", "text": "0"}, {"speaker": "A", "text": "2"}]})

    def test_partials_and_server_errors_fail(self):
        for message in ({"type": "Error"}, {"type": "Turn", "end_of_turn": False}):
            with self.assertRaises(RuntimeError):
                app.Transcript().accept(message)


if __name__ == "__main__":
    unittest.main()
