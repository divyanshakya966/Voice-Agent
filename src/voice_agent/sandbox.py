from __future__ import annotations

import os
import signal
import subprocess
from pathlib import Path

from .util import which

BWRAP = "bwrap"


class SandboxError(RuntimeError):
    pass


def available() -> bool:
    return bool(which(BWRAP))


def git_root(cwd: Path) -> Path | None:
    if not which("git"):
        return None
    env = {
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_PAGER": "cat",
    }
    try:
        result = subprocess.run(
            ["git", "-C", str(cwd), "rev-parse", "--show-toplevel"],
            text=True,
            capture_output=True,
            timeout=10,
            env={**os.environ, **env},
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    path = Path(result.stdout.strip())
    return path.resolve() if path.is_dir() else None


def _mount_args(bind_root: Path, cwd: Path, allow_network: bool, path_prefix: str) -> list[str]:
    argv = [
        BWRAP,
        "--unshare-all",
        "--die-with-parent",
        "--new-session",
        "--cap-drop",
        "ALL",
        "--ro-bind",
        "/usr",
        "/usr",
        "--symlink",
        "usr/lib",
        "/lib",
        "--symlink",
        "usr/lib64",
        "/lib64",
        "--symlink",
        "usr/bin",
        "/bin",
        "--symlink",
        "usr/sbin",
        "/sbin",
        "--ro-bind",
        "/etc",
        "/etc",
        *(["--ro-bind", "/usr/local", "/usr/local"] if Path("/usr/local").is_dir() else []),
        "--dev",
        "/dev",
        "--proc",
        "/proc",
        "--tmpfs",
        "/tmp",
        "--bind",
        str(bind_root),
        str(bind_root),
        "--chdir",
        str(cwd),
        "--clearenv",
        "--setenv",
        "PATH",
        path_prefix,
        "--setenv",
        "HOME",
        "/tmp",
        "--setenv",
        "TMPDIR",
        "/tmp",
        "--setenv",
        "LANG",
        "C.UTF-8",
        "--setenv",
        "LC_ALL",
        "C.UTF-8",
        "--setenv",
        "GIT_CONFIG_NOSYSTEM",
        "1",
        "--setenv",
        "GIT_CONFIG_GLOBAL",
        "/dev/null",
        "--setenv",
        "GIT_TERMINAL_PROMPT",
        "0",
        "--setenv",
        "GIT_PAGER",
        "cat",
        "--setenv",
        "PAGER",
        "cat",
        "--setenv",
        "GIT_EXTERNAL_DIFF",
        "",
        "--setenv",
        "PYTHONDONTWRITEBYTECODE",
        "1",
        "--setenv",
        "npm_config_cache",
        "/tmp/npm-cache",
        "--setenv",
        "NO_COLOR",
        "1",
    ]
    if allow_network:
        argv.append("--share-net")
    argv.append("--")
    return argv


def run_sandboxed(
    argv: list[str],
    *,
    cwd: Path,
    bind_root: Path,
    allow_network: bool = False,
    timeout: float = 90,
) -> tuple[int, str]:
    if not available():
        raise SandboxError("bubblewrap (bwrap) is missing; command execution is disabled.")
    path_entries = ["/usr/bin", "/bin", "/usr/local/bin"]
    for candidate in (cwd / ".venv" / "bin", bind_root / ".venv" / "bin"):
        if candidate.is_dir():
            path_entries.insert(0, str(candidate))
    command = _mount_args(bind_root, cwd, allow_network, ":".join(path_entries)) + argv
    try:
        proc = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
            env={"PATH": "/usr/bin:/bin"},
        )
    except OSError as exc:
        raise SandboxError(f"Cannot start sandbox: {exc}") from exc
    try:
        output, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except OSError:
            proc.kill()
        output, _ = proc.communicate(timeout=5)
        return 124, (output or "") + f"\n[timed out after {timeout:.0f}s]"
    return proc.returncode or 0, output or ""
