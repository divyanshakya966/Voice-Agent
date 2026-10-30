from __future__ import annotations

import array
import math
import os
import select
import subprocess
import sys
import tempfile
import time
import wave
from collections import deque
from collections.abc import Callable
from pathlib import Path

from .config import CACHE_DIR, cfg_get
from .util import which


class AudioError(RuntimeError):
    pass


def _rms(chunk: bytes) -> float:
    samples = array.array("h")
    samples.frombytes(chunk[: len(chunk) // 2 * 2])
    if not samples:
        return 0.0
    return math.sqrt(sum(value * value for value in samples) / len(samples))


def _write_wav(path: Path, chunks: list[bytes], rate: int) -> None:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"".join(chunks))


def _make_beep(path: Path, frequency: float, duration: float = 0.07, volume: float = 0.12) -> None:
    rate = 44100
    count = int(rate * duration)
    frames = array.array("h")
    fade = max(1, int(rate * 0.006))
    for index in range(count):
        envelope = 1.0
        if index < fade:
            envelope = index / fade
        elif index > count - fade:
            envelope = (count - index) / fade
        value = int(32767 * volume * envelope * math.sin(2 * math.pi * frequency * index / rate))
        frames.append(value)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(frames.tobytes())


def _beep_path(name: str, frequency: float) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / name
    if not path.exists():
        _make_beep(path, frequency)
    return path


def play_file(
    path: str | Path,
    timeout: float = 30,
    stop_requested: Callable[[], bool] | None = None,
) -> bool:
    players = [
        ("paplay", ["paplay"]),
        ("pw-play", ["pw-play"]),
        ("aplay", ["aplay", "-q"]),
    ]
    for name, argv in players:
        if not which(name):
            continue
        process: subprocess.Popen | None = None
        try:
            process = subprocess.Popen(
                [*argv, str(path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            deadline = time.monotonic() + timeout
            while process.poll() is None:
                if stop_requested and stop_requested():
                    process.terminate()
                    process.wait(timeout=2)
                    return False
                if time.monotonic() >= deadline:
                    process.kill()
                    process.wait(timeout=2)
                    break
                time.sleep(0.05)
            if process.returncode == 0:
                return True
        except (OSError, subprocess.SubprocessError):
            if process and process.poll() is None:
                process.kill()
            continue
    return False


def beep(kind: str, enabled: bool = True) -> None:
    if not enabled:
        return
    try:
        if kind == "start":
            play_file(_beep_path("beep-start.wav", 880), timeout=5)
        elif kind == "stop":
            play_file(_beep_path("beep-stop.wav", 520), timeout=5)
        elif kind == "error":
            play_file(_beep_path("beep-error.wav", 320), timeout=5)
    except (OSError, AudioError):
        pass


def _capture_command(rate: int, source: str) -> list[str]:
    if not which("parec"):
        raise AudioError("parec is missing. Install pulseaudio-utils or pipewire-pulseaudio.")
    argv = [
        "parec",
        "--format=s16le",
        f"--rate={rate}",
        "--channels=1",
        "--latency-msec=30",
        "--raw",
    ]
    if source:
        argv.append(f"--device={source}")
    return argv


def record_utterance(
    config: dict,
    *,
    start_timeout: float | None = None,
    max_seconds: float | None = None,
    silence_ms: int | None = None,
    calibration_ms: int | None = None,
    beeps: bool | None = None,
    stop_requested: Callable[[], bool] | None = None,
    speech_started: Callable[[], None] | None = None,
    capture_ready: Callable[[], None] | None = None,
) -> Path | None:
    rate = int(cfg_get(config, "audio.rate", 16000))
    chunk_ms = int(cfg_get(config, "audio.chunk_ms", 50))
    chunk_bytes = int(rate * chunk_ms / 1000) * 2
    calibration_ms = int(calibration_ms if calibration_ms is not None else cfg_get(config, "audio.calibration_ms", 350))
    min_rms = float(cfg_get(config, "audio.min_rms", 180))
    noise_multiplier = float(cfg_get(config, "audio.noise_multiplier", 2.8))
    speech_chunks_needed = int(cfg_get(config, "audio.speech_chunks", 3))
    silence_ms = int(silence_ms if silence_ms is not None else cfg_get(config, "audio.silence_ms", 5000))
    max_seconds = float(max_seconds if max_seconds is not None else cfg_get(config, "audio.max_seconds", 20))
    start_timeout = float(start_timeout if start_timeout is not None else cfg_get(config, "audio.start_timeout", 6))
    beeps = bool(beeps if beeps is not None else cfg_get(config, "audio.beeps", False))
    source = str(cfg_get(config, "audio.source", "") or "")

    argv = _capture_command(rate, source)
    try:
        proc = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
            start_new_session=True,
        )
    except OSError as exc:
        raise AudioError(f"Cannot start microphone capture: {exc}") from exc
    if proc.stdout is None:
        proc.kill()
        raise AudioError("Microphone capture did not provide an output stream.")
    fd = proc.stdout.fileno()

    chunks: list[bytes] = []
    pre_roll: deque[bytes] = deque(maxlen=max(2, int(300 / chunk_ms)))
    calibration: list[float] = []
    threshold = max(min_rms, 250.0)
    speech_run = 0
    silence_run = 0
    in_speech = False
    calibrated = False
    speech_started_at = 0.0
    started_at = time.monotonic()
    last_data = started_at
    buffer = b""
    ready_notified = False

    print("Listening...", file=sys.stderr, flush=True)
    try:
        while True:
            if stop_requested and stop_requested():
                break
            now = time.monotonic()
            timeout = 0.25
            try:
                ready, _, _ = select.select([fd], [], [], timeout)
            except (OSError, ValueError):
                break
            if not ready:
                if proc.poll() is not None:
                    break
                if now - last_data > 3.0:
                    break
                if calibrated and not in_speech and now - started_at > start_timeout:
                    break
                continue
            try:
                data = os.read(fd, chunk_bytes * 4)
            except OSError:
                break
            if not data:
                break
            last_data = time.monotonic()
            buffer += data
            while len(buffer) >= chunk_bytes:
                chunk = buffer[:chunk_bytes]
                buffer = buffer[chunk_bytes:]
                rms = _rms(chunk)
                elapsed = time.monotonic() - started_at

                if not calibrated:
                    calibration.append(rms)
                    if elapsed * 1000 >= calibration_ms:
                        ordered = sorted(calibration)
                        noise = ordered[max(0, int(len(ordered) * 0.2) - 1)] if ordered else 0.0
                        threshold = max(min_rms, min(noise * noise_multiplier, 3200.0))
                        calibrated = True
                        if capture_ready and not ready_notified:
                            capture_ready()
                            ready_notified = True
                    continue

                if not in_speech:
                    pre_roll.append(chunk)
                    if rms >= threshold:
                        speech_run += 1
                    else:
                        speech_run = 0
                    if speech_run >= speech_chunks_needed:
                        in_speech = True
                        speech_started_at = elapsed
                        if speech_started:
                            speech_started()
                        chunks.extend(pre_roll)
                        pre_roll.clear()
                    elif elapsed > start_timeout:
                        break
                else:
                    chunks.append(chunk)
                    if rms >= threshold:
                        silence_run = 0
                    else:
                        silence_run += 1
                    if silence_run * chunk_ms >= silence_ms:
                        beep("stop", beeps)
                        break
                    if elapsed - speech_started_at >= max_seconds:
                        beep("stop", beeps)
                        break
            if not calibrated and time.monotonic() - started_at > (calibration_ms / 1000 + 1.0):
                calibrated = True
                threshold = max(min_rms, 250.0)
                if capture_ready and not ready_notified:
                    capture_ready()
                    ready_notified = True
            if time.monotonic() - started_at > start_timeout + max_seconds + 3:
                break
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)
        except OSError:
            pass
        try:
            proc.stdout.close()
        except OSError:
            pass

    if stop_requested and stop_requested():
        return None
    if not in_speech or not chunks:
        print("No speech detected.", file=sys.stderr, flush=True)
        return None

    duration = len(chunks) * chunk_ms / 1000
    if duration < 0.25:
        print("Utterance too short.", file=sys.stderr, flush=True)
        return None
    fd_out, name = tempfile.mkstemp(prefix="voice-agent-", suffix=".wav", dir=str(CACHE_DIR))
    os.close(fd_out)
    path = Path(name)
    _write_wav(path, chunks, rate)
    print(f"Captured {duration:.1f}s.", file=sys.stderr, flush=True)
    return path


def record_fixed(config: dict, seconds: float) -> Path:
    rate = int(cfg_get(config, "audio.rate", 16000))
    source = str(cfg_get(config, "audio.source", "") or "")
    argv = _capture_command(rate, source)
    chunk_bytes = int(rate * 0.05) * 2
    chunks: list[bytes] = []
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
    if proc.stdout is None:
        proc.kill()
        raise AudioError("Fixed capture did not provide an output stream.")
    deadline = time.monotonic() + seconds
    try:
        while time.monotonic() < deadline:
            data = proc.stdout.read(chunk_bytes)
            if not data:
                break
            chunks.append(data)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)
        proc.stdout.close()
    fd, name = tempfile.mkstemp(prefix="voice-agent-", suffix=".wav", dir=str(CACHE_DIR))
    os.close(fd)
    path = Path(name)
    _write_wav(path, chunks, rate)
    return path
