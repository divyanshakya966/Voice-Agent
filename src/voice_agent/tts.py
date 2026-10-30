from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Callable

from .audio import play_file
from .config import CACHE_DIR, cfg_get, expand_path, find_executable
from .util import run, which

_FENCE = re.compile(r"```[\s\S]*?```")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_URL = re.compile(r"https?://\S+")
_MARKDOWN = re.compile(r"[*_#>|]+")
_THINK = re.compile(r"^[\s\S]*?<｜end▁of▁thinking｜>", re.MULTILINE)


def _speakable(text: str, limit: int) -> str:
    cleaned = _THINK.sub("", text)
    cleaned = _FENCE.sub(" Code is shown in the terminal. ", cleaned)
    cleaned = _LINK.sub(r"\1", cleaned)
    cleaned = _URL.sub(" ", cleaned)
    cleaned = _MARKDOWN.sub(" ", cleaned)
    cleaned = re.sub(r"`([^`]*)`", r"\1", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if len(cleaned) > limit:
        cut = cleaned[:limit]
        boundary = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
        if boundary > limit // 2:
            cut = cut[: boundary + 1]
        cleaned = cut.strip() + " Full details are in the terminal."
    return cleaned


def _piper_speak(text: str, config: dict, stop_requested: Callable[[], bool] | None = None) -> bool:
    piper = find_executable(str(cfg_get(config, "paths.piper", "")), "piper")
    voice = expand_path(str(cfg_get(config, "paths.piper_voice", "")))
    if not piper or not voice.is_file():
        return False
    timeout = float(cfg_get(config, "tts.timeout", 60))
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="tts-", suffix=".wav", dir=str(CACHE_DIR))
    os.close(fd)
    path = Path(name)
    try:
        result = run([piper, "-m", str(voice), "-f", str(path), "-q"], timeout=timeout, input_text=text)
        if result.returncode != 0 or not path.exists() or path.stat().st_size == 0:
            return False
        return play_file(path, timeout=timeout, stop_requested=stop_requested)
    except (OSError, RuntimeError):
        return False
    finally:
        path.unlink(missing_ok=True)


def _espeak_speak(text: str, config: dict, stop_requested: Callable[[], bool] | None = None) -> bool:
    engine = str(cfg_get(config, "tts.fallback", "espeak-ng"))
    if not which(engine):
        return False
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="tts-", suffix=".wav", dir=str(CACHE_DIR))
    os.close(fd)
    path = Path(name)
    try:
        result = run([engine, "-s", "172", "-w", str(path), text], timeout=60)
        if result.returncode != 0 or not path.exists():
            return False
        return play_file(path, timeout=60, stop_requested=stop_requested)
    except (OSError, RuntimeError):
        return False
    finally:
        path.unlink(missing_ok=True)


def speak(
    text: str,
    config: dict,
    *,
    force: bool = False,
    stop_requested: Callable[[], bool] | None = None,
) -> bool:
    if not text.strip():
        return False
    if not force and not bool(cfg_get(config, "tts.enabled", True)):
        return False
    limit = int(cfg_get(config, "tts.max_chars", 700))
    spoken = _speakable(text, limit)
    if not spoken:
        return False
    print(f"Assistant: {text}", flush=True)
    if _piper_speak(spoken, config, stop_requested):
        return True
    if _espeak_speak(spoken, config, stop_requested):
        return True
    print("TTS unavailable; response was printed only.", file=sys.stderr, flush=True)
    return False
