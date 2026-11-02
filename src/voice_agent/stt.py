from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

from .config import CACHE_DIR, cfg_get, expand_path, find_executable
from .util import run


class STTError(RuntimeError):
    pass


_JUNK = re.compile(
    r"(?:\[\s*(?:blank_audio|silence|music|applause|noise|inaudible|"
    r"unintelligible|background noise|static|disturbance)\s*\]"
    r"|\[\s*non[- ]english speech\s*\]"
    r"|\(\s*(?:silence|music|applause|noise|inaudible|unintelligible|"
    r"background noise|static|disturbance)\s*\)"
    r"|\(\s*speaking in (?:a )?foreign language\s*\))",
    re.IGNORECASE,
)
_NON_SPEECH_ONLY = re.compile(
    r"(?:blank audio|silence|music|applause|noise|inaudible|unintelligible|"
    r"background noise|static|disturbance|non[- ]english speech|"
    r"speaking in (?:a )?foreign language)[.!?]*",
    re.IGNORECASE,
)
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def whisper_binary(config: dict) -> str | None:
    configured = str(cfg_get(config, "paths.whisper_cli", ""))
    return find_executable(configured, "whisper-cli")


def whisper_model(config: dict) -> Path:
    return expand_path(str(cfg_get(config, "paths.whisper_model", "")))


def _clean(text: str) -> str:
    text = _ANSI.sub("", text)
    text = _JUNK.sub(" ", text)
    text = re.sub(r"^\s*\[[0-9:.\s\->]+\]\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\s+", " ", text)
    text = text.strip()
    return "" if _NON_SPEECH_ONLY.fullmatch(text) else text


def transcribe(wav_path: str | Path, config: dict) -> str:
    binary = whisper_binary(config)
    if not binary:
        raise STTError("whisper-cli not found. Run scripts/setup.sh first.")
    model = whisper_model(config)
    if not model.is_file():
        raise STTError(f"Whisper model not found: {model}. Run scripts/setup.sh first.")
    language = str(cfg_get(config, "stt.language", "en"))
    threads = int(cfg_get(config, "stt.threads", 8))
    threads = max(1, min(threads, os.cpu_count() or 1))
    timeout = float(cfg_get(config, "stt.timeout", 120))
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    fd, prefix = tempfile.mkstemp(prefix="stt-", dir=str(CACHE_DIR))
    os.close(fd)
    prefix_path = Path(prefix)
    prefix_path.unlink(missing_ok=True)
    argv = [
        binary,
        "-m",
        str(model),
        "-f",
        str(wav_path),
        "-l",
        language,
        "-t",
        str(threads),
        "-nt",
        "-np",
        "-otxt",
        "-of",
        str(prefix_path),
    ]
    try:
        result = run(argv, timeout=timeout)
    except OSError as exc:
        raise STTError(f"Cannot run whisper-cli: {exc}") from exc
    txt_path = Path(str(prefix_path) + ".txt")
    text = ""
    if txt_path.exists():
        text = txt_path.read_text(encoding="utf-8", errors="replace")
        txt_path.unlink(missing_ok=True)
    if not text:
        text = result.stdout or ""
    text = _clean(text)
    if result.returncode != 0 and not text:
        detail = (result.stderr or "").strip().splitlines()
        raise STTError(detail[-1] if detail else f"whisper-cli failed with {result.returncode}")
    return text
