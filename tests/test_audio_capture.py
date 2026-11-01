import array
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from voice_agent import audio


class _FakeStream:
    def fileno(self) -> int:
        return 7

    def close(self) -> None:
        pass


class _FakeProcess:
    def __init__(self) -> None:
        self.stdout = _FakeStream()
        self.terminate = Mock()
        self.wait = Mock()
        self.kill = Mock()

    def poll(self) -> None:
        return None


def _pcm(value: int) -> bytes:
    return bytes(array.array("h", [value]) * 800)


class AudioCaptureTests(unittest.TestCase):
    def _capture(self, chunks: list[bytes], **overrides):
        process = _FakeProcess()
        clock = [0.0]
        chunk_index = [0]

        def fake_select(*args):
            clock[0] += 0.05
            return ([7], [], [])

        def fake_read(fd: int, size: int) -> bytes:
            index = min(chunk_index[0], len(chunks) - 1)
            chunk_index[0] += 1
            return chunks[index]

        config = {
            "audio": {
                "rate": 16000,
                "chunk_ms": 50,
                "calibration_ms": 350,
                "min_rms": 180,
                "noise_multiplier": 2.8,
                "speech_chunks": 3,
                "silence_ms": 4500,
                "no_speech_timeout_ms": 5000,
                "start_timeout": 6,
                "max_seconds": 20,
            }
        }
        config["audio"].update(overrides)
        with TemporaryDirectory() as directory, patch.object(audio, "CACHE_DIR", Path(directory)), patch.object(
            audio, "_capture_command", return_value=["parec"]
        ), patch.object(audio, "desktop_popen", return_value=process), patch.object(
            audio.select, "select", side_effect=fake_select
        ), patch.object(audio.os, "read", side_effect=fake_read), patch.object(
            audio.time, "monotonic", side_effect=lambda: clock[0]
        ):
            result = audio.record_utterance(config)
        return result, clock[0]

    def test_parec_ends_after_configured_trailing_silence(self) -> None:
        chunks = [_pcm(0)] * 7 + [_pcm(2000)] * 3 + [_pcm(0)] * 100

        result, elapsed = self._capture(chunks)

        self.assertIsNotNone(result)
        self.assertGreaterEqual(elapsed, 4.5)
        self.assertLess(elapsed, 6.5)
        assert result is not None
        result.unlink(missing_ok=True)

    def test_parec_no_speech_does_not_reach_watchdog(self) -> None:
        result, elapsed = self._capture([_pcm(0)] * 700)

        self.assertIsNone(result)
        self.assertGreaterEqual(elapsed, 5.0)
        self.assertLess(elapsed, 7.0)


if __name__ == "__main__":
    unittest.main()
