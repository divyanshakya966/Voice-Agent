from __future__ import annotations

import difflib
import os
import re
import shlex
import shutil
import stat
from datetime import datetime
from pathlib import Path

from ..config import STATE_DIR, cfg_get, expand_path
from ..sandbox import SandboxError, git_root, run_sandboxed
from ..util import atomic_write, redact, trim
from .base import Tool, ToolContext, ToolError

SENSITIVE_DIRS = {
    ".ssh",
    ".gnupg",
    ".password-store",
    ".mozilla",
    ".thunderbird",
    ".config",
    "keyrings",
    ".local/share/keyrings",
    "wallet",
}
SENSITIVE_NAMES = {
    ".env",
    ".netrc",
    ".git-credentials",
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "credentials",
    "secrets",
    "secrets.json",
    "wallet.dat",
}
SENSITIVE_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".kdbx", ".ovpn"}
SKIP_DIRS = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    "dist",
    "build",
    "target",
    ".tox",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
}


def _roots(config: dict, key: str) -> list[Path]:
    values = cfg_get(config, f"agent.{key}", []) or []
    return [expand_path(value) for value in values]


def _under(path: Path, roots: list[Path]) -> bool:
    for root in roots:
        if path == root or root in path.parents:
            return True
    return False


def _sensitive(path: Path) -> bool:
    lowered = [part.lower() for part in path.parts]
    for bad in SENSITIVE_DIRS:
        bad_parts = bad.lower().split("/")
        for index in range(len(lowered) - len(bad_parts) + 1):
            if lowered[index : index + len(bad_parts)] == bad_parts:
                return True
    name = path.name.lower()
    return name in SENSITIVE_NAMES or any(name.endswith(suffix) for suffix in SENSITIVE_SUFFIXES)


def _case_insensitive(path: Path, roots: list[Path]) -> Path | None:
    for root in roots:
        try:
            relative = path.relative_to(root)
        except ValueError:
            continue
        current = root
        for part in relative.parts:
            candidate = current / part
            if candidate.exists():
                current = candidate
                continue
            variants = {
                part.casefold(),
                part.replace("_", "-").casefold(),
                part.replace("-", "_").casefold(),
            }
            try:
                matches = [item for item in current.iterdir() if item.name.casefold() in variants]
            except OSError:
                return None
            if len(matches) != 1:
                return None
            current = matches[0]
        if current.exists():
            return current.resolve()
    return None


def _resolve(path_value: str | None, ctx: ToolContext, *, write: bool) -> Path:
    raw = path_value or str(ctx.cwd)
    path = Path(os.path.expandvars(os.path.expanduser(raw)))
    if not path.is_absolute():
        path = ctx.cwd / path
    try:
        path = path.resolve(strict=False)
    except OSError as exc:
        raise ToolError(f"Cannot resolve path: {exc}") from exc
    roots = _roots(ctx.config, "write_roots" if write else "allowed_read_roots")
    if not roots:
        raise ToolError("No allowed filesystem roots are configured.")
    if not write and not path.exists():
        alternate = _case_insensitive(path, roots)
        if alternate is not None:
            path = alternate
    if not _under(path, roots):
        shown = ", ".join(str(root) for root in roots)
        raise ToolError(f"Path is outside the allowed roots: {shown}")
    if _sensitive(path):
        raise ToolError("Path is sensitive and blocked by policy.")
    return path


def _list_files(args: dict, ctx: ToolContext) -> str:
    path = _resolve(args.get("path"), ctx, write=False)
    try:
        if path.is_file():
            details = path.stat()
            return f"{path} (file, {details.st_size} bytes)"
        if not path.is_dir():
            raise ToolError(f"Path does not exist: {path}")
        entries = sorted(path.iterdir(), key=lambda item: item.name.lower())
    except OSError as exc:
        raise ToolError(f"Cannot list {path}: {exc}") from exc
    lines = [f"{path}:"]
    for entry in entries[:200]:
        try:
            if entry.is_symlink():
                kind = "link"
                size = ""
            elif entry.is_dir():
                kind = "dir"
                size = ""
            else:
                kind = "file"
                size = str(entry.stat(follow_symlinks=False).st_size)
        except OSError:
            kind, size = "unknown", ""
        lines.append(f"  {entry.name}  [{kind}{(' ' + size) if size else ''}]")
    if len(entries) > 200:
        lines.append(f"  ... {len(entries) - 200} more entries")
    return "\n".join(lines)


def _read_file(args: dict, ctx: ToolContext) -> str:
    path = _resolve(args.get("path"), ctx, write=False)
    if not path.is_file():
        raise ToolError(f"Not a regular file: {path}")
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ToolError(f"Cannot read {path}: {exc}") from exc
    if b"\x00" in data[:4096]:
        raise ToolError("Binary files are not supported.")
    max_chars = max(500, min(20000, int(args.get("max_chars", 12000))))
    text = data.decode("utf-8", errors="replace")
    suffix = "" if len(text) <= max_chars else f"\n...[truncated {len(text) - max_chars} chars]"
    return f"{path}:\n{redact(text[:max_chars])}{suffix}"


def _search_files(args: dict, ctx: ToolContext) -> str:
    pattern_text = str(args.get("pattern", "")).strip()
    if not pattern_text:
        raise ToolError("Search pattern is empty.")
    try:
        pattern = re.compile(pattern_text, re.IGNORECASE)
    except re.error as exc:
        raise ToolError(f"Invalid regular expression: {exc}") from exc
    root = _resolve(args.get("path"), ctx, write=False)
    if not root.is_dir():
        raise ToolError(f"Search path is not a directory: {root}")
    max_results = max(1, min(200, int(args.get("max_results", 80))))
    matches: list[str] = []
    scanned = 0
    for current, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [name for name in dirs if name not in SKIP_DIRS and not name.startswith(".git")]
        for filename in files:
            if len(matches) >= max_results or scanned >= 4000:
                break
            path = Path(current) / filename
            if _sensitive(path):
                continue
            try:
                if path.stat().st_size > 1_500_000:
                    continue
                data = path.read_bytes()
            except OSError:
                continue
            if b"\x00" in data[:4096]:
                continue
            scanned += 1
            text = data.decode("utf-8", errors="replace")
            for line_number, line in enumerate(text.splitlines(), start=1):
                if pattern.search(line):
                    snippet = redact(line.strip())[:300]
                    matches.append(f"{path}:{line_number}: {snippet}")
                    if len(matches) >= max_results:
                        break
        if len(matches) >= max_results or scanned >= 4000:
            break
    if not matches:
        return f"No matches for {pattern_text!r} under {root}."
    return "\n".join(matches)


def _diff(old: str, new: str, path: Path) -> str:
    diff = list(
        difflib.unified_diff(
            old.splitlines(),
            new.splitlines(),
            fromfile=f"a/{path.name}",
            tofile=f"b/{path.name}",
            lineterm="",
            n=2,
        )
    )
    return trim("\n".join(diff), 8000)


def _backup(path: Path) -> Path | None:
    if not path.exists():
        return None
    backup_dir = STATE_DIR / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = backup_dir / f"{stamp}-{path.name}"
    counter = 1
    while target.exists():
        target = backup_dir / f"{stamp}-{counter}-{path.name}"
        counter += 1
    shutil.copy2(path, target)
    return target


def _write_file(args: dict, ctx: ToolContext) -> str:
    path = _resolve(args.get("path"), ctx, write=True)
    content = str(args.get("content", ""))
    if len(content.encode("utf-8")) > 300_000:
        raise ToolError("Content is too large (limit 300 KB).")
    try:
        old = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
    except OSError as exc:
        raise ToolError(f"Cannot read existing file: {exc}") from exc
    if old == content:
        return f"{path} already has that content; nothing changed."
    if path.exists() and path.is_dir():
        raise ToolError(f"Path is a directory: {path}")
    backup = _backup(path)
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
    try:
        atomic_write(path, content, mode=mode)
    except OSError as exc:
        raise ToolError(f"Cannot write {path}: {exc}") from exc
    ctx.audit.write("file_write", path=str(path), chars=len(content), backup=str(backup) if backup else None)
    message = f"Wrote {len(content)} characters to {path}."
    if backup:
        message += f" Backup: {backup}."
    diff = _diff(old, content, path)
    if diff:
        message += "\n" + diff
    return message


def _apply_patch(args: dict, ctx: ToolContext) -> str:
    path = _resolve(args.get("path"), ctx, write=True)
    old_text = str(args.get("old_text", ""))
    new_text = str(args.get("new_text", ""))
    if not old_text:
        raise ToolError("old_text is empty.")
    if not path.is_file():
        raise ToolError(f"Not a regular file: {path}")
    try:
        current = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ToolError(f"Cannot read {path}: {exc}") from exc
    occurrences = current.count(old_text)
    if occurrences != 1:
        raise ToolError(f"old_text must occur exactly once; found {occurrences} occurrences.")
    updated = current.replace(old_text, new_text, 1)
    backup = _backup(path)
    mode = stat.S_IMODE(path.stat().st_mode)
    try:
        atomic_write(path, updated, mode=mode)
    except OSError as exc:
        raise ToolError(f"Cannot write {path}: {exc}") from exc
    ctx.audit.write("file_patch", path=str(path), backup=str(backup) if backup else None)
    return f"Patched {path}." + (f" Backup: {backup}." if backup else "") + "\n" + _diff(current, updated, path)


ALLOWED_GIT = {
    "status",
    "diff",
    "log",
    "show",
    "branch",
    "rev-parse",
    "ls-files",
    "grep",
    "blame",
    "describe",
    "shortlog",
    "remote",
}


def _reject_shell_tokens(tokens: list[str]) -> None:
    bad = set(";|&><`$")
    for token in tokens:
        if any(char in token for char in bad):
            raise ToolError(f"Shell metacharacter rejected: {token!r}")


def _validate_check(command: str) -> list[str]:
    try:
        tokens = shlex.split(command)
    except ValueError as exc:
        raise ToolError(f"Cannot parse command: {exc}") from exc
    if not tokens:
        raise ToolError("Command is empty.")
    _reject_shell_tokens(tokens)
    executable = tokens[0]
    if "/" in executable or "\\" in executable:
        raise ToolError("Use a command name, not a path.")
    args = tokens[1:]

    if executable == "git":
        if not args:
            raise ToolError("git needs a subcommand.")
        if args[0].startswith("-"):
            raise ToolError("Global git options are not allowed; start with a subcommand.")
        for arg in args:
            if arg.startswith("-c") and not arg.startswith("--"):
                raise ToolError("git -c is not allowed.")
            if arg.startswith(("--config-env", "--exec-path")):
                raise ToolError("That git option is not allowed.")
        subcommand = args[0]
        if subcommand not in ALLOWED_GIT:
            raise ToolError(f"git subcommand not allowed: {subcommand}")
        if subcommand in {"diff", "show"}:
            args = [args[0], "--no-ext-diff", "--no-textconv", *args[1:]]
        if subcommand == "branch":
            allowed = {"-a", "--all", "-r", "--remotes", "-v", "-vv", "--verbose", "--list", "--show-current"}
            if any(not arg.startswith("-") for arg in args[1:]) or any(arg not in allowed for arg in args[1:]):
                raise ToolError("git branch is limited to listing branches.")
        if subcommand == "remote":
            extra = args[1:]
            if (
                extra
                and extra not in (["-v"], ["--verbose"], ["show"], ["get-url", extra[-1]])
                and not (extra[0] in {"show", "get-url"} and len(extra) <= 2)
            ):
                raise ToolError("git remote is limited to listing and URL reads.")
            if extra and extra[0] == "get-url" and len(extra) != 2:
                raise ToolError("git remote get-url needs exactly one remote name.")
        if subcommand == "grep":
            for arg in args[1:]:
                if arg in {"-O", "--open-files-in-pager"} or arg.startswith("--open-files-in-pager="):
                    raise ToolError("git grep pager options are not allowed.")
        return ["git", *args]

    if executable in {"python", "python3"}:
        if len(args) < 2 or args[0] != "-m":
            raise ToolError("Python runs are limited to -m pytest, -m unittest, -m compileall, or -m pip check.")
        module = args[1]
        if module in {"pytest", "unittest", "compileall"}:
            return [executable, *args]
        if module == "pip":
            if len(args) < 3 or args[2] not in {"check", "list", "show"}:
                raise ToolError("python -m pip is limited to check, list, or show.")
            return [executable, *args]
        raise ToolError(f"Python module not allowed: {module}")

    if executable in {"pytest", "mypy", "bandit"}:
        return tokens

    if executable == "ruff":
        if not args or args[0] not in {"check", "format"}:
            raise ToolError("ruff is limited to check or format --check.")
        if args[0] == "format" and "--check" not in args:
            raise ToolError("ruff format requires --check.")
        return tokens

    if executable in {"npm", "pnpm", "yarn"}:
        if not args:
            raise ToolError(f"{executable} needs test or run <script>.")
        if args[0] == "test":
            return tokens
        if args[0] == "run" and len(args) >= 2 and args[1] in {"test", "lint", "typecheck", "build", "check"}:
            return tokens
        raise ToolError(f"{executable} is limited to test or run test/lint/typecheck/build/check.")

    if executable == "cargo":
        if not args or args[0] not in {"test", "check", "build", "clippy", "fmt"}:
            raise ToolError("cargo is limited to test, check, build, clippy, or fmt --check.")
        if args[0] == "fmt" and "--check" not in args:
            raise ToolError("cargo fmt requires --check.")
        return tokens

    if executable == "go":
        if not args or args[0] not in {"test", "vet", "build"}:
            raise ToolError("go is limited to test, vet, or build.")
        return tokens

    raise ToolError(f"Command is not allowlisted: {executable}")


def _run_check(args: dict, ctx: ToolContext) -> str:
    command = str(args.get("command", "")).strip()
    argv = _validate_check(command)
    cwd_value = str(args.get("cwd") or ctx.cwd)
    cwd = Path(os.path.expandvars(os.path.expanduser(cwd_value)))
    if not cwd.is_absolute():
        cwd = ctx.cwd / cwd
    try:
        cwd = cwd.resolve(strict=False)
    except OSError as exc:
        raise ToolError(f"Cannot resolve cwd: {exc}") from exc
    write_roots = _roots(ctx.config, "write_roots")
    if not _under(cwd, write_roots):
        raise ToolError("Command cwd is outside the write roots.")
    if not cwd.is_dir():
        raise ToolError(f"cwd is not a directory: {cwd}")
    bind_root = git_root(cwd) or cwd
    if not _under(bind_root, write_roots):
        bind_root = cwd
    allow_network = bool(cfg_get(ctx.config, "agent.sandbox_network", False))
    timeout = float(cfg_get(ctx.config, "agent.command_timeout", 90))
    try:
        code, output = run_sandboxed(
            argv,
            cwd=cwd,
            bind_root=bind_root,
            allow_network=allow_network,
            timeout=timeout,
        )
    except SandboxError as exc:
        raise ToolError(str(exc)) from exc
    limit = int(cfg_get(ctx.config, "agent.command_output_limit", 20000))
    cleaned = redact(output.strip())
    if not cleaned:
        cleaned = "(no output)"
    return f"$ {command}\nexit={code}\n{trim(cleaned, limit)}"


def build_tools(ctx: ToolContext) -> list[Tool]:
    return [
        Tool(
            "list_files",
            "List a directory.",
            {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "additionalProperties": False,
            },
            _list_files,
            summary=lambda args: f"list {args.get('path', '.')}",
        ),
        Tool(
            "read_file",
            "Read a text file; secrets redacted.",
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "max_chars": {"type": "integer", "minimum": 500, "maximum": 20000},
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            _read_file,
            summary=lambda args: f"read {args.get('path', '')}",
        ),
        Tool(
            "search_files",
            "Regex search files.",
            {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path": {"type": "string"},
                    "max_results": {"type": "integer", "minimum": 1, "maximum": 200},
                },
                "required": ["pattern"],
                "additionalProperties": False,
            },
            _search_files,
            summary=lambda args: f"search for {args.get('pattern', '')}",
        ),
        Tool(
            "write_file",
            "Write a file (prompts, backup).",
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
            _write_file,
            dangerous=True,
            summary=lambda args: f"write {len(str(args.get('content', '')))} chars to {args.get('path', '')}",
        ),
        Tool(
            "apply_patch",
            "Replace one exact text occurrence (prompts, backup).",
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_text": {"type": "string"},
                    "new_text": {"type": "string"},
                },
                "required": ["path", "old_text", "new_text"],
                "additionalProperties": False,
            },
            _apply_patch,
            dangerous=True,
            summary=lambda args: f"patch {args.get('path', '')}",
        ),
        Tool(
            "run_check",
            "Run an allowlisted dev check in a network-isolated sandbox (prompts).",
            {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "cwd": {"type": "string"},
                },
                "required": ["command"],
                "additionalProperties": False,
            },
            _run_check,
            dangerous=True,
            summary=lambda args: f"run `{args.get('command', '')}`",
        ),
    ]
