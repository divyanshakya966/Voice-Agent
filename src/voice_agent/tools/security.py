from __future__ import annotations

import os
import re
import stat
from pathlib import Path

from ..util import redact, run, trim
from .base import Tool, ToolContext, ToolError
from .files import SENSITIVE_NAMES, SKIP_DIRS, _resolve, _sensitive

SECRET_PATTERNS = [
    ("private-key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("aws-access-key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("github-token", re.compile(r"(gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})")),
    ("slack-token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")),
    ("openai-key", re.compile(r"sk-[A-Za-z0-9]{20,}")),
    ("generic-secret", re.compile(r"(?i)(api[_-]?key|secret|token|password|passwd)\s*[=:]\s*['\"]?[A-Za-z0-9_./+@-]{8,}")),
]


def _secret_scan(root: Path) -> list[str]:
    findings: list[str] = []
    files_seen = 0
    for current, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [name for name in dirs if name not in SKIP_DIRS and not name.startswith(".git")]
        for filename in files:
            if len(findings) >= 60 or files_seen >= 3000:
                break
            path = Path(current) / filename
            if filename.lower() in SENSITIVE_NAMES:
                findings.append(f"{path}: sensitive file present (contents not read)")
                continue
            if _sensitive(path):
                continue
            try:
                info = path.stat(follow_symlinks=False)
                if not stat.S_ISREG(info.st_mode) or info.st_size > 1_000_000:
                    continue
                data = path.read_bytes()
            except OSError:
                continue
            if b"\x00" in data[:2048]:
                continue
            files_seen += 1
            text = data.decode("utf-8", errors="replace")
            for line_number, line in enumerate(text.splitlines(), start=1):
                for name, pattern in SECRET_PATTERNS:
                    if pattern.search(line):
                        findings.append(f"{path}:{line_number}: possible {name}")
                        break
                if len(findings) >= 60:
                    break
        if len(findings) >= 60 or files_seen >= 3000:
            break
    return findings


def _permission_scan(root: Path) -> list[str]:
    findings: list[str] = []
    paths = [root]
    if root.is_dir():
        for current, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = [name for name in dirs if name not in SKIP_DIRS]
            paths.extend(Path(current) / name for name in files)
            if len(paths) > 1500:
                break
    for path in paths[:1500]:
        try:
            info = path.stat(follow_symlinks=False)
        except OSError:
            continue
        mode = info.st_mode
        flags = []
        if mode & 0o002:
            flags.append("world-writable")
        if mode & stat.S_ISUID:
            flags.append("setuid")
        if mode & stat.S_ISGID:
            flags.append("setgid")
        if flags:
            findings.append(f"{path}: {', '.join(flags)} ({oct(stat.S_IMODE(mode))})")
    return findings


def _command(argv: list[str], timeout: float = 10) -> str:
    try:
        result = run(argv, timeout=timeout)
    except (OSError, Exception):
        return "unavailable"
    text = (result.stdout or result.stderr or "").strip()
    return text or "unavailable"


def _ssh_config() -> list[str]:
    path = Path("/etc/ssh/sshd_config")
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ["sshd config: unreadable"]
    wanted = {"passwordauthentication", "permitrootlogin", "pubkeyauthentication", "permitemptypasswords"}
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key = stripped.split(None, 1)[0].lower()
        if key in wanted:
            lines.append(stripped)
    return lines or ["sshd config: no key directives found"]


def _suid_count() -> str:
    count = 0
    examples: list[str] = []
    for directory in (Path("/usr/bin"), Path("/usr/sbin"), Path("/usr/local/bin")):
        if not directory.is_dir():
            continue
        try:
            entries = list(directory.iterdir())
        except OSError:
            continue
        for path in entries:
            try:
                info = path.stat(follow_symlinks=False)
            except OSError:
                continue
            if info.st_mode & (stat.S_ISUID | stat.S_ISGID):
                count += 1
                if len(examples) < 8:
                    examples.append(str(path))
    return f"{count} setuid/setgid binaries" + (f" (e.g. {', '.join(examples)})" if examples else "")


def _security_scan(args: dict, ctx: ToolContext) -> str:
    raw_target = args.get("target")
    if not raw_target or str(raw_target).strip().lower() in {"summary", "workspace", "current", ".", "here"}:
        raw_target = None
    target = _resolve(raw_target, ctx, write=False)
    if not target.exists():
        raise ToolError(f"Target does not exist: {target}")
    secret_findings = _secret_scan(target) if target.is_dir() else []
    permission_findings = _permission_scan(target)

    listening = _command(["ss", "-tuln"])
    failed_system = _command(["systemctl", "--failed", "--no-pager", "--plain"])
    failed_user = _command(["systemctl", "--user", "--failed", "--no-pager", "--plain"])
    firewall = _command(["firewall-cmd", "--state"])
    selinux = _command(["getenforce"])
    kernel = _command(["uname", "-r"])
    journals = _command(["journalctl", "-p", "err", "-n", "12", "--no-pager", "-o", "cat"], timeout=15)
    ssh = _ssh_config()

    sections = [
        "Secret scan:\n" + ("\n".join(secret_findings) if secret_findings else "no obvious secrets found"),
        "Permission scan:\n" + ("\n".join(permission_findings) if permission_findings else "no risky modes found in scanned files"),
        f"Listening sockets:\n{trim(listening, 2500)}",
        f"Firewall: {firewall}\nSELinux: {selinux}\nKernel: {kernel}",
        f"Failed system services:\n{trim(failed_system, 1500)}\nFailed user services:\n{trim(failed_user, 1500)}",
        "SSH daemon settings:\n" + "\n".join(ssh),
        f"SUID/SGID:\n{_suid_count()}",
        f"Recent errors:\n{trim(redact(journals), 2500)}",
    ]
    return trim("\n\n".join(sections), 14000)


def build_tools(ctx: ToolContext) -> list[Tool]:
    return [
        Tool(
            "security_scan",
            "Read-only security scan of the workspace.",
            {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "Directory or file to scan. Omit to scan the current workspace.",
                    }
                },
                "additionalProperties": False,
            },
            _security_scan,
            summary=lambda args: f"security scan {args.get('target', 'workspace')}",
        )
    ]
