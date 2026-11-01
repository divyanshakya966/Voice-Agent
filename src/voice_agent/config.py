from __future__ import annotations

import json
import os
from pathlib import Path

APP = "voice-agent"
HOME = Path.home()
DATA_DIR = Path(os.environ.get("XDG_DATA_HOME", HOME / ".local" / "share")) / APP
STATE_DIR = Path(os.environ.get("XDG_STATE_HOME", HOME / ".local" / "state")) / APP
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", HOME / ".cache")) / APP
CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", HOME / ".config")) / APP
CONFIG_PATH = CONFIG_DIR / "config.json"

DEFAULT_APPS = {
    "browser": ["firefox"],
    "spotify": ["spotify"],
    "firefox": ["firefox"],
    "files": ["dolphin"],
    "dolphin": ["dolphin"],
    "terminal": ["konsole"],
    "konsole": ["konsole"],
    "settings": ["systemsettings"],
    "systemsettings": ["systemsettings"],
    "image viewer": ["gwenview"],
    "gwenview": ["gwenview"],
    "elisa": ["elisa"],
    "screenshot tool": ["spectacle"],
    "spectacle": ["spectacle"],
}

DEFAULT_CONFIG = {
    "paths": {
        "whisper_cli": str(DATA_DIR / "bin" / "whisper-cli"),
        "whisper_model": str(DATA_DIR / "models" / "ggml-base.en.bin"),
        "piper": str(DATA_DIR / "piper" / "piper"),
        "piper_voice": str(DATA_DIR / "models" / "en_US-lessac-medium.onnx"),
    },
    "audio": {
        "source": "",
        "backend": "parec",
        "rate": 16000,
        "chunk_ms": 50,
        "calibration_ms": 350,
        "min_rms": 180,
        "noise_multiplier": 2.8,
        "speech_chunks": 3,
        "silence_ms": 4500,
        "no_speech_timeout_ms": 5000,
        "wake_silence_ms": 900,
        "max_seconds": 20,
        "start_timeout": 6,
        "barge_in": False,
    },
    "stt": {
        "language": "en",
        "threads": 8,
        "timeout": 120,
        "confirm_transcript": False,
        "confirmation_retries": 1,
        "confirmation_max_chars": 180,
    },
    "tts": {
        "enabled": True,
        "engine": "piper",
        "fallback": "espeak-ng",
        "max_chars": 350,
        "timeout": 60,
        "status_updates": True,
        "acknowledgement": True,
    },
    "llm": {
        "url": "http://127.0.0.1:11434",
        "model": "qwen3:1.7b",
        "keep_alive": "30m",
        "num_ctx": 4096,
        "num_predict": 192,
        "temperature": 0.2,
        "timeout": 180,
        "max_steps": 6,
        "thinking": False,
    },
    "agent": {
        "confirmation": "gui",
        "session_history_messages": 24,
        "session_history_chars": 12000,
        "allowed_read_roots": [
            "~/GitHub",
            "~/Projects",
            "~/Documents",
            "~/Desktop",
            "~/Music",
        ],
        "write_roots": [
            "~/GitHub",
        ],
        "sandbox_network": False,
        "command_timeout": 90,
        "command_output_limit": 20000,
        "apps": DEFAULT_APPS,
        "media_player": "elisa",
        "music_roots": ["~/Music"],
        "services": {},
        "power_actions": ["suspend", "hibernate", "poweroff", "reboot"],
        "plugin_modules": [],
        "browser": {
            "enabled": False,
            "engine": "chromium",
            "headless": True,
            "allowed_domains": [],
            "profile_dir": str(DATA_DIR / "browser-profile"),
        },
    },
}


def _deep_merge(base: dict, user: dict) -> dict:
    result = dict(base)
    for key, value in user.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def ensure_dirs() -> None:
    for directory in (DATA_DIR, STATE_DIR, CACHE_DIR, CONFIG_DIR):
        directory.mkdir(parents=True, exist_ok=True)


def load_config(create: bool = True) -> dict:
    ensure_dirs()
    user: dict = {}
    if CONFIG_PATH.exists():
        try:
            user = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Cannot read config {CONFIG_PATH}: {exc}") from exc
    elif not create:
        raise FileNotFoundError(CONFIG_PATH)
    config = _deep_merge(DEFAULT_CONFIG, user)
    old_default_roots = ["~/GitHub", "~/Projects", "~/Documents", "~/Desktop"]
    configured_roots = config.get("agent", {}).get("allowed_read_roots")
    if configured_roots == old_default_roots:
        config["agent"]["allowed_read_roots"] = [*configured_roots, "~/Music"]
    if create and not CONFIG_PATH.exists():
        save_config(config)
    return config


def save_config(config: dict) -> None:
    ensure_dirs()
    tmp = CONFIG_PATH.with_suffix(".json.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(config, handle, indent=2)
        handle.write("\n")
    os.replace(tmp, CONFIG_PATH)


def cfg_get(config: dict, dotted: str, default=None):
    value = config
    for part in dotted.split("."):
        if not isinstance(value, dict) or part not in value:
            return default
        value = value[part]
    return value


def expand_path(value: str | os.PathLike[str]) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(str(value)))).resolve(strict=False)


def find_executable(configured: str, name: str) -> str | None:
    path = expand_path(configured)
    if path.is_file() and os.access(path, os.X_OK):
        return str(path)
    from .util import which

    return which(name)
