import unittest

from voice_agent.stt import _clean


class SpeechCleanupTests(unittest.TestCase):
    def test_known_non_speech_markers_are_silent(self) -> None:
        for text in (
            "[BLANK_AUDIO]",
            "[noise]",
            "(background noise)",
            "music.",
            "applause",
            "inaudible",
            "unintelligible!",
            "[non-English speech]",
            "speaking in a foreign language",
        ):
            with self.subTest(text=text):
                self.assertEqual(_clean(text), "")

    def test_non_speech_marker_does_not_discard_real_text(self) -> None:
        self.assertEqual(_clean("[noise] call mom"), "call mom")

    def test_real_short_request_is_preserved(self) -> None:
        self.assertEqual(_clean("stop"), "stop")


if __name__ == "__main__":
    unittest.main()