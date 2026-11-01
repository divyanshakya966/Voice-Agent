from __future__ import annotations

import array
import math
import os
import queue
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
from .util import desktop_popen, which


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
            process = desktop_popen(
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
    speech_chunks_needed = max(1, int(cfg_get(config, "audio.speech_chunks", 3)))
    silence_ms = int(silence_ms if silence_ms is not None else cfg_get(config, "audio.silence_ms", 4500))
    no_speech_timeout_ms = int(cfg_get(config, "audio.no_speech_timeout_ms", 5000))
    max_seconds = float(max_seconds if max_seconds is not None else cfg_get(config, "audio.max_seconds", 20))
    start_timeout = float(start_timeout if start_timeout is not None else cfg_get(config, "audio.start_timeout", 6))
    source = str(cfg_get(config, "audio.source", "") or "")
    backend = str(cfg_get(config, "audio.backend", "parec")).lower()
    if backend == "sounddevice":
        return _record_sounddevice(
            config,
            rate=rate,
            chunk_ms=chunk_ms,
            calibration_ms=calibration_ms,
            min_rms=min_rms,
            silence_ms=silence_ms,
            max_seconds=max_seconds,
            start_timeout=start_timeout,
            stop_requested=stop_requested,
            speech_started=speech_started,
            capture_ready=capture_ready,
        )
    if backend != "parec":
        raise AudioError("audio.backend must be parec or sounddevice.")

    argv = _capture_command(rate, source)
    try:
        proc = desktop_popen(
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
    calibrated_at = 0.0

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
            stop_capture = False
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
                        calibrated_at = time.monotonic()
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
                    elif time.monotonic() - calibrated_at >= min(
                        start_timeout,
                        max(0.0, no_speech_timeout_ms / 1000),
                    ):
                        stop_capture = True
                        break
                else:
                    chunks.append(chunk)
                    if rms >= threshold:
                        silence_run = 0
                    else:
                        silence_run += 1
                    if silence_run * chunk_ms >= silence_ms:
                        stop_capture = True
                        break
                    if elapsed - speech_started_at >= max_seconds:
                        stop_capture = True
                        break
            if stop_capture:
                break
            if not calibrated and time.monotonic() - started_at > (calibration_ms / 1000 + 1.0):
                calibrated = True
                threshold = max(min_rms, 250.0)
                calibrated_at = time.monotonic()
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


def _record_sounddevice(
    config: dict,
    *,
    rate: int,
    chunk_ms: int,
    calibration_ms: int,
    min_rms: float,
    silence_ms: int,
    max_seconds: float,
    start_timeout: float,
    stop_requested: Callable[[], bool] | None,
    speech_started: Callable[[], None] | None,
    capture_ready: Callable[[], None] | None,
) -> Path | None:
    try:
        import sounddevice as sd
        import webrtcvad
    except (ImportError, OSError) as exc:
        raise AudioError("sounddevice streaming requires requirements-streaming.txt.") from exc

    chunk_ms = 20
    chunk_samples = int(rate * chunk_ms / 1000)
    speech_chunks_needed = max(1, int(cfg_get(config, "audio.speech_chunks", 3)))
    audio_queue: queue.Queue[bytes] = queue.Queue(maxsize=20)
    vad = webrtcvad.Vad(2)

    def callback(indata, frames, time_info, status) -> None:
        try:
            audio_queue.put_nowait(bytes(indata))
        except queue.Full:
            pass

    chunks: list[bytes] = []
    pre_roll: deque[bytes] = deque(maxlen=max(2, int(300 / chunk_ms)))
    started_at = time.monotonic()
    speech_started_at = 0.0
    speech_run = 0
    silence_run = 0
    in_speech = False
    ready = False
    try:
        with sd.RawInputStream(
            samplerate=rate,
            blocksize=chunk_samples,
            channels=1,
            dtype="int16",
            device=str(cfg_get(config, "audio.source", "") or "") or None,
            callback=callback,
        ):
            if capture_ready:
                capture_ready()
            ready = True
            while True:
                if stop_requested and stop_requested():
                    return None
                elapsed = time.monotonic() - started_at
                if not in_speech and elapsed > start_timeout:
                    break
                try:
                    chunk = audio_queue.get(timeout=0.25)
                except queue.Empty:
                    continue
                is_speech = vad.is_speech(chunk, rate)
                if not in_speech:
                    pre_roll.append(chunk)
                    speech_run = speech_run + 1 if is_speech else 0
                    if speech_run >= speech_chunks_needed:
                        in_speech = True
                        speech_started_at = elapsed
                        if speech_started:
                            speech_started()
                        chunks.extend(pre_roll)
                        pre_roll.clear()
                else:
                    chunks.append(chunk)
                    silence_run = 0 if is_speech else silence_run + chunk_ms
                    if silence_run >= silence_ms or elapsed - speech_started_at >= max_seconds:
                        break
    except Exception as exc:
        if not ready:
            raise AudioError(f"Cannot start sounddevice capture: {exc}") from exc
        raise AudioError(f"Sounddevice capture failed: {exc}") from exc
    if stop_requested and stop_requested():
        return None
    if not chunks or not in_speech:
        return None
    fd_out, name = tempfile.mkstemp(prefix="voice-agent-", suffix=".wav", dir=str(CACHE_DIR))
    os.close(fd_out)
    path = Path(name)
    _write_wav(path, chunks, rate)
    return path


def record_fixed(config: dict, seconds: float) -> Path:
    if seconds <= 0:
        raise AudioError("Recording duration must be greater than zero.")
    rate = int(cfg_get(config, "audio.rate", 16000))
    source = str(cfg_get(config, "audio.source", "") or "")
    if str(cfg_get(config, "audio.backend", "parec")).lower() == "sounddevice":
        return _record_sounddevice_fixed(rate, source, seconds)
    argv = _capture_command(rate, source)
    chunk_bytes = int(rate * 0.05) * 2
    chunks: list[bytes] = []
    proc = desktop_popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
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


def _record_sounddevice_fixed(rate: int, source: str, seconds: float) -> Path:
    try:
        import sounddevice as sd
    except (ImportError, OSError) as exc:
        raise AudioError("sounddevice recording requires requirements-streaming.txt.") from exc

    chunk_samples = max(1, int(rate * 0.02))
    audio_queue: queue.Queue[bytes] = queue.Queue(maxsize=100)

    def callback(indata, frames, time_info, status) -> None:
        try:
            audio_queue.put_nowait(bytes(indata))
        except queue.Full:
            pass

    chunks: list[bytes] = []
    deadline = time.monotonic() + seconds
    try:
        with sd.RawInputStream(
            samplerate=rate,
            blocksize=chunk_samples,
            channels=1,
            dtype="int16",
            device=source or None,
            callback=callback,
        ):
            while time.monotonic() < deadline:
                try:
                    chunks.append(audio_queue.get(timeout=min(0.25, max(0.01, deadline - time.monotonic()))))
                except queue.Empty:
                    continue
    except Exception as exc:
        raise AudioError(f"Sounddevice fixed recording failed: {exc}") from exc

    if not chunks:
        raise AudioError("Sounddevice returned no audio data.")
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="voice-agent-", suffix=".wav", dir=str(CACHE_DIR))
    os.close(fd)
    path = Path(name)
    _write_wav(path, chunks, rate)
    return path
