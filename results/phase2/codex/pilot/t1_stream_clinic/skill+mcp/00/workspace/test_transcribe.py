import unittest

from transcribe import MODEL, PARAMETERS, TranscriptionError, Turns, validate_begin


class TranscriptTests(unittest.TestCase):
    def test_final_turns_are_ordered_and_replaced_with_latest_text(self):
        turns = Turns()
        for order, text in [(2, "Second."), (1, "First"), (1, "First.")]:
            turns.accept({"type": "Turn", "turn_order": order,
                          "end_of_turn": True, "speaker_label": "A", "transcript": text})
        turns.accept({"type": "SpeakerRevision", "revisions": [
            {"turn_order": 1, "speaker_label": "B"}]})
        turns.accept({"type": "SpeakerRevision", "revisions": [
            {"turn_order": 1, "speaker_label": "C"}]})
        self.assertEqual(turns.result(), {"turns": [
            {"speaker": "C", "text": "First."},
            {"speaker": "A", "text": "Second."}]})

    def test_partials_fail(self):
        with self.assertRaises(TranscriptionError):
            Turns().accept({"type": "Turn", "end_of_turn": False})

    def test_required_settings_must_be_confirmed(self):
        configuration = {"model": MODEL, "domain": "medical-v1",
                         "speaker_labels": True, "redact_pii": True}
        validate_begin({"configuration": configuration})
        for key in configuration:
            with self.subTest(key=key), self.assertRaises(TranscriptionError):
                validate_begin({"configuration": {k: v for k, v in configuration.items()
                                                   if k != key}})
        self.assertEqual(PARAMETERS["include_partial_turns"], "false")
        self.assertNotIn("redact_pii_policies", PARAMETERS)


if __name__ == "__main__":
    unittest.main()
