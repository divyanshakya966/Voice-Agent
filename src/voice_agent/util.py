from __future__ import annotations

import datetime as _dt
import os
import re
import shutil
import subprocess
from pathlib import Path

_SECRET_PATTERNS = [
    re.compile(r"(?i)(aws_secret_access_key|aws_access_key_id)\s*[=:]\s*\S+"),
    re.compile(r"(?i)(api[_-]?key|apikey|secret|token|password|passwd|pwd)\s*[=:]\s*['\"]?[^\s'\"]{6,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
]


def redact(text: str) -> str:
    cleaned = text
    for pattern in _SECRET_PATTERNS:
        cleaned = pattern.sub("[REDACTED]", cleaned)
    return cleaned


def run(
    argv: list[str],
    *,
    timeout: float | None = 30,
    input_text: str | None = None,
    cwd: str | Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    return subprocess.run(
        argv,
        input=input_text,
        text=True,
        capture_output=True,
        timeout=timeout,
        cwd=str(cwd) if cwd else None,
        env=full_env,
        check=False,
    )


def which(name: str) -> str | None:
    return shutil.which(name)


def trim(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[truncated {len(text) - limit} chars]"


def notify(title: str, body: str = "", urgency: str = "normal") -> None:
    if not which("notify-send"):
        return
    try:
        run(["notify-send", "--app-name=Jarvis", f"--urgency={urgency}", title, body], timeout=5)
    except (OSError, subprocess.SubprocessError):
        pass


def now_iso() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def atomic_write(path: Path, data: str, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".voice-agent.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
        handle.write(data)
    os.replace(tmp, path)
