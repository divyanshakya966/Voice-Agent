from __future__ import annotations

import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from ..config import cfg_get, expand_path
from ..util import run, trim, which
from .base import Tool, ToolContext, ToolError

SINK = "@DEFAULT_AUDIO_SINK@"


def _status_volume() -> str:
    result = run(["wpctl", "get-volume", SINK], timeout=5)
    raw = result.stdout.strip()
    match = re.search(r"Volume:\s*([0-9.]+)", raw)
    if match:
        percent = round(float(match.group(1)) * 100)
        muted = "muted" if "[MUTED]" in raw else "unmuted"
        return f"Volume: {percent}% ({muted})."
    return raw or "Volume status unavailable."


def _volume(args: dict, ctx: ToolContext) -> str:
    if not which("wpctl"):
        raise ToolError("wpctl is missing. Install wireplumber or pipewire-utils.")
    action = str(args.get("action", "status")).lower()
    if action == "status":
        return _status_volume()
    if action == "set":
        percent = float(args.get("percent", 50))
        percent = max(0.0, min(100.0, percent))
        result = run(["wpctl", "set-volume", SINK, f"{percent:.0f}%"], timeout=5)
    elif action in {"up", "down"}:
        step = max(1.0, min(50.0, float(args.get("percent", 10))))
        sign = "+" if action == "up" else "-"
        result = run(["wpctl", "set-volume", SINK, f"{step:.0f}%{sign}"], timeout=5)
    elif action in {"mute", "unmute", "toggle_mute"}:
        value = {"mute": "1", "unmute": "0", "toggle_mute": "toggle"}[action]
        result = run(["wpctl", "set-mute", SINK, value], timeout=5)
    else:
        raise ToolError(f"Unsupported volume action: {action}")
    if result.returncode != 0:
        raise ToolError(result.stderr.strip() or "Volume command failed.")
    return _status_volume()


def _battery(args: dict, ctx: ToolContext) -> str:
    for base in sorted(Path("/sys/class/power_supply").glob("BAT*")):
        try:
            capacity = (base / "capacity").read_text().strip()
            status = (base / "status").read_text().strip().lower()
        except OSError:
            continue
        return f"Battery: {capacity}% ({status})."
    raise ToolError("No battery was found on this system.")


def _mpris_names() -> list[str]:
    result = run(
        [
            "dbus-send",
            "--session",
            "--dest=org.freedesktop.DBus",
            "--type=method_call",
            "--print-reply",
            "/org/freedesktop/DBus",
            "org.freedesktop.DBus.ListNames",
        ],
        timeout=5,
    )
    return [line.strip().strip('"') for line in result.stdout.splitlines() if "org.mpris.MediaPlayer2." in line]


def _media(args: dict, ctx: ToolContext) -> str:
    action = str(args.get("action", "play_pause")).lower()
    if which("playerctl"):
        mapping = {
            "play_pause": "play-pause",
            "play": "play",
            "pause": "pause",
            "next": "next",
            "previous": "previous",
            "stop": "stop",
            "status": "status",
        }
        if action not in mapping:
            raise ToolError(f"Unsupported media action: {action}")
        result = run(["playerctl", mapping[action]], timeout=5)
        if result.returncode != 0:
            raise ToolError(result.stderr.strip() or "No media player is running.")
        return result.stdout.strip() or f"Media {action} sent."
    names = _mpris_names()
    if not names:
        raise ToolError("No media player is running.")
    name = names[0]
    methods = {
        "play_pause": "PlayPause",
        "play": "Play",
        "pause": "Pause",
        "next": "Next",
        "previous": "Previous",
        "stop": "Stop",
    }
    if action == "status":
        result = run(
            [
                "dbus-send",
                "--session",
                f"--dest={name}",
                "--type=method_call",
                "--print-reply",
                "/org/mpris/MediaPlayer2",
                "org.freedesktop.DBus.Properties.Get",
                "string:org.mpris.MediaPlayer2.Player",
                "string:PlaybackStatus",
            ],
            timeout=5,
        )
        return result.stdout.strip() or "Media status unavailable."
    if action not in methods:
        raise ToolError(f"Unsupported media action: {action}")
    result = run(
        [
            "dbus-send",
            "--session",
            f"--dest={name}",
            "--type=method_call",
            "/org/mpris/MediaPlayer2",
            f"org.mpris.MediaPlayer2.Player.{methods[action]}",
        ],
        timeout=5,
    )
    if result.returncode != 0:
        raise ToolError(result.stderr.strip() or "Media command failed.")
    return f"Media {action} sent to {name}."


def _lock(args: dict, ctx: ToolContext) -> str:
    result = run(["loginctl", "lock-session"], timeout=10)
    if result.returncode == 0:
        return "Screen locked."
    if which("qdbus-qt6"):
        result = run(
            ["qdbus-qt6", "org.kde.screensaver", "/ScreenSaver", "org.freedesktop.ScreenSaver.Lock"],
            timeout=10,
        )
        if result.returncode == 0:
            return "Screen locked."
    raise ToolError(result.stderr.strip() or "Could not lock the screen.")


def _screenshot(args: dict, ctx: ToolContext) -> str:
    if not which("spectacle"):
        raise ToolError("spectacle is missing. Install spectacle.")
    directory = expand_path("~/Pictures/Screenshots")
    directory.mkdir(parents=True, exist_ok=True)
    name = "voice-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".png"
    path = directory / name
    result = run(["spectacle", "-b", "-n", "-f", "-o", str(path)], timeout=30)
    if result.returncode != 0 or not path.exists():
        raise ToolError(result.stderr.strip() or "Screenshot failed.")
    return f"Screenshot saved to {path}"


def _notify(args: dict, ctx: ToolContext) -> str:
    message = str(args.get("message", "")).strip()
    if not message:
        raise ToolError("Notification message is empty.")
    if ctx.notifier:
        ctx.notifier("Voice Agent", message[:500])
    elif which("notify-send"):
        run(["notify-send", "--app-name=Voice Agent", "Voice Agent", message[:500]], timeout=5)
    else:
        raise ToolError("notify-send is missing.")
    return "Notification sent."


def _system_info(args: dict, ctx: ToolContext) -> str:
    query = str(args.get("query", "summary")).lower()
    commands = {
        "memory": ["free", "-h"],
        "disk": ["df", "-h", str(Path.home())],
        "uptime": ["uptime"],
        "gpu": [
            "nvidia-smi",
            "--query-gpu=name,memory.total,memory.used,utilization.gpu,temperature.gpu",
            "--format=csv,noheader",
        ],
        "cpu": ["lscpu"],
        "kernel": ["uname", "-a"],
    }
    order = ["memory", "disk", "uptime", "gpu", "kernel"] if query in {"summary", "all"} else [query]
    if any(item not in commands for item in order):
        raise ToolError("Unknown system info query. Use memory, disk, uptime, gpu, cpu, kernel, or summary.")
    sections = []
    for item in order:
        result = run(commands[item], timeout=10)
        output = (result.stdout or result.stderr).strip()
        if not output:
            output = "unavailable"
        if item == "cpu":
            lines = [line for line in output.splitlines() if re.search(r"Model name|CPU\(s\)|Thread|Core|MHz", line)]
            output = "\n".join(lines[:12])
        sections.append(f"{item}:\n{trim(output, 2500)}")
    return "\n\n".join(sections)


def _open_app(args: dict, ctx: ToolContext) -> str:
    requested = str(args.get("name", "")).strip().lower()
    apps = cfg_get(ctx.config, "agent.apps", {}) or {}
    normalized = {str(key).lower(): value for key, value in apps.items()}
    if requested not in normalized:
        names = ", ".join(sorted(normalized))
        raise ToolError(f"App '{requested}' is not allowlisted. Available: {names}")
    argv = normalized[requested]
    if not isinstance(argv, list) or not argv or not all(isinstance(part, str) for part in argv):
        raise ToolError("Invalid app entry in config.")
    if not shutil.which(argv[0]):
        raise ToolError(f"Executable not found: {argv[0]}")
    try:
        subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            env=os.environ.copy(),
        )
    except OSError as exc:
        raise ToolError(f"Cannot launch {requested}: {exc}") from exc
    return f"Opened {requested}."


def _clipboard_read(args: dict, ctx: ToolContext) -> str:
    command = "wl-paste" if which("wl-paste") else ("xclip" if which("xclip") else None)
    if command == "wl-paste":
        result = run(["wl-paste", "--no-newline"], timeout=5)
    elif command == "xclip":
        result = run(["xclip", "-selection", "clipboard", "-o"], timeout=5)
    else:
        raise ToolError("No clipboard tool found. Install wl-clipboard.")
    if result.returncode != 0:
        raise ToolError(result.stderr.strip() or "Clipboard is empty.")
    return trim(result.stdout, 5000) or "Clipboard is empty."


def _clipboard_write(args: dict, ctx: ToolContext) -> str:
    text = str(args.get("text", ""))
    if which("wl-copy"):
        result = run(["wl-copy"], timeout=5, input_text=text)
    elif which("xclip"):
        result = run(["xclip", "-selection", "clipboard"], timeout=5, input_text=text)
    else:
        raise ToolError("No clipboard tool found. Install wl-clipboard.")
    if result.returncode != 0:
        raise ToolError(result.stderr.strip() or "Clipboard write failed.")
    return f"Copied {len(text)} characters to the clipboard."


def build_tools(ctx: ToolContext) -> list[Tool]:
    app_names = sorted((cfg_get(ctx.config, "agent.apps", {}) or {}).keys())
    return [
        Tool(
            "open_app",
            "Open an allowlisted app by name.",
            {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "enum": app_names},
                },
                "required": ["name"],
                "additionalProperties": False,
            },
            _open_app,
            summary=lambda args: f"open app {args.get('name', '')}",
        ),
        Tool(
            "volume",
            "Read or change default volume.",
            {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["status", "set", "up", "down", "mute", "unmute", "toggle_mute"],
                    },
                    "percent": {"type": "number", "minimum": 0, "maximum": 100},
                },
                "required": ["action"],
                "additionalProperties": False,
            },
            _volume,
            summary=lambda args: f"volume {args.get('action', '')}",
        ),
        Tool(
            "media",
            "Control the active media player.",
            {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["play_pause", "play", "pause", "next", "previous", "stop", "status"],
                    }
                },
                "required": ["action"],
                "additionalProperties": False,
            },
            _media,
            summary=lambda args: f"media {args.get('action', '')}",
        ),
        Tool(
            "lock_screen",
            "Lock the session.",
            {"type": "object", "properties": {}, "additionalProperties": False},
            _lock,
            summary=lambda args: "lock the screen",
        ),
        Tool(
            "screenshot",
            "Capture the screen.",
            {"type": "object", "properties": {}, "additionalProperties": False},
            _screenshot,
            summary=lambda args: "take a screenshot",
        ),
        Tool(
            "notify",
            "Show a notification.",
            {
                "type": "object",
                "properties": {"message": {"type": "string", "maxLength": 500}},
                "required": ["message"],
                "additionalProperties": False,
            },
            _notify,
            summary=lambda args: "show a notification",
        ),
        Tool(
            "system_info",
            "Read system status: memory, disk, uptime, gpu, cpu, kernel, summary.",
            {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "enum": ["summary", "all", "memory", "disk", "uptime", "gpu", "cpu", "kernel"],
                    }
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            _system_info,
            summary=lambda args: f"read {args.get('query', 'summary')} info",
        ),
        Tool(
            "battery",
            "Read battery charge and status.",
            {"type": "object", "properties": {}, "additionalProperties": False},
            _battery,
            summary=lambda args: "read the battery status",
        ),
        Tool(
            "clipboard_read",
            "Read clipboard (prompts).",
            {"type": "object", "properties": {}, "additionalProperties": False},
            _clipboard_read,
            dangerous=True,
            summary=lambda args: "read the clipboard",
        ),
        Tool(
            "clipboard_write",
            "Copy text to clipboard.",
            {
                "type": "object",
                "properties": {"text": {"type": "string", "maxLength": 20000}},
                "required": ["text"],
                "additionalProperties": False,
            },
            _clipboard_write,
            summary=lambda args: f"copy {len(str(args.get('text', '')))} characters",
        ),
    ]
