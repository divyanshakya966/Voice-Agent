from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

from . import llm, stt, tts
from .agent import Agent
from .audio import AudioError, record_fixed, record_utterance
from .audit import AuditLog
from .config import CONFIG_DIR, CONFIG_PATH, DATA_DIR, STATE_DIR, cfg_get, expand_path, load_config, save_config
from .sandbox import available as bwrap_available
from .sandbox import run_sandboxed
from .tools import build_registry
from .util import atomic_write, notify, run, which


def _ok(label: str, condition: bool, detail: str = "", critical: bool = False) -> bool:
    if condition:
        print(f"[ok]   {label}" + (f"  {detail}" if detail else ""))
        return True
    print(f"[{'FAIL' if critical else 'warn'}] {label}" + (f"  {detail}" if detail else ""))
    return not critical


def _doctor(args) -> int:
    try:
        config = load_config()
    except Exception as exc:
        print(f"[FAIL] config: {exc}")
        return 1
    critical_ok = True
    print(f"config: {CONFIG_PATH}")
    print(f"data:   {DATA_DIR}")

    whisper = stt.whisper_binary(config)
    critical_ok &= _ok("whisper-cli", bool(whisper), whisper or "missing", critical=True)
    model = stt.whisper_model(config)
    critical_ok &= _ok("whisper model", model.is_file(), str(model), critical=True)
    piper = expand_path(str(cfg_get(config, "paths.piper", "")))
    voice = expand_path(str(cfg_get(config, "paths.piper_voice", "")))
    _ok("piper binary", piper.is_file(), str(piper))
    _ok("piper voice", voice.is_file(), str(voice))
    _ok("espeak-ng fallback", bool(which("espeak-ng")), "", critical=False)

    try:
        ollama_version = llm.version(config)
        _ok("ollama", True, ollama_version, critical=True)
        models = {item.get("name", "") for item in llm.tags(config)}
        configured = str(cfg_get(config, "llm.model", "qwen3:1.7b"))
        _ok(f"llm model {configured}", configured in models, "not in ollama list" if configured not in models else "")
    except Exception as exc:
        critical_ok = False
        print(f"[FAIL] ollama  {exc}")

    gpu = run(["nvidia-smi", "--query-gpu=name,memory.total,memory.used", "--format=csv,noheader"], timeout=10)
    _ok("nvidia gpu", gpu.returncode == 0, gpu.stdout.strip() or gpu.stderr.strip())

    _ok("parec", bool(which("parec")), "", critical=True)
    _ok("audio playback", any(which(name) for name in ("paplay", "pw-play", "aplay")), "", critical=True)
    try:
        info = run(["pactl", "info"], timeout=5)
        source = next((line.split(":", 1)[1].strip() for line in info.stdout.splitlines() if line.startswith("Default Source:")), "")
        _ok("default source", bool(source), source)
    except Exception:
        _ok("default source", False, "pactl unavailable")

    bwrap_ok = bwrap_available()
    critical_ok &= _ok("bubblewrap", bwrap_ok, "sandboxed checks available" if bwrap_ok else "command execution disabled", critical=True)
    if bwrap_available():
        write_roots = [expand_path(value) for value in cfg_get(config, "agent.write_roots", [])]
        test_cwd = next((root for root in write_roots if root.is_dir()), None)
        if test_cwd:
            try:
                code, output = run_sandboxed(["/usr/bin/echo", "sandbox-ok"], cwd=test_cwd, bind_root=test_cwd)
                _ok("sandbox test", code == 0 and "sandbox-ok" in output, output.strip())
            except Exception as exc:
                _ok("sandbox test", False, str(exc))
        else:
            _ok("sandbox test", False, "no write root exists")

    _ok("kdialog", bool(which("kdialog")), "GUI confirmations")
    _ok("spectacle", bool(which("spectacle")), "screenshots")
    _ok("wpctl", bool(which("wpctl")), "volume")
    _ok("wl-clipboard", bool(which("wl-copy") and which("wl-paste")), "clipboard")
    _ok("playerctl", bool(which("playerctl")), "optional; MPRIS fallback is used")
    _ok("notify-send", bool(which("notify-send")), "desktop notifications")

    print("\nStatus:", "ready" if critical_ok else "needs attention")
    print("Tip: set agent.confirmation to gui, voice, terminal, or auto in config.json.")
    return 0 if critical_ok else 1


def _init(args) -> int:
    config = load_config()
    save_config(config)
    print(f"Config ready: {CONFIG_PATH}")
    print(f"Data dir:     {DATA_DIR}")
    print("Next: run scripts/setup.sh if models are missing, then voice-agent doctor.")
    return 0


def _install_desktop(args) -> int:
    executable = which("voice-agent")
    if not executable:
        candidate = Path(__file__).resolve().parents[2] / "bin" / "voice-agent"
        executable = str(candidate)
    if not Path(executable).exists():
        print(f"Launcher not found: {executable}", file=sys.stderr)
        return 1
    directory = Path.home() / ".local" / "share" / "applications"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "voice-agent.desktop"
    content = f"""[Desktop Entry]
Type=Application
Name=Voice Agent
Comment=Continuous local voice assistant
Exec="{executable}" wake
Icon=audio-input-microphone
Terminal=false
NoDisplay=false
Categories=Utility;
Keywords=voice;assistant;local;
X-KDE-StartupNotify=false
"""
    atomic_write(path, content, mode=0o644)
    print(f"Installed {path}")
    print("KDE shortcut: System Settings > Keyboard > Shortcuts > Custom Shortcuts")
    print(f"  Wake command: {executable} wake")
    print(f"  Stop command: {executable} stop")
    return 0


WAKE_STOP_PATH = STATE_DIR / "wake.stop"
WAKE_LOCK_PATH = STATE_DIR / "wake.lock"


def _stop(args) -> int:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    atomic_write(WAKE_STOP_PATH, "stop\n", mode=0o600)
    config = load_config()
    try:
        llm.unload(config)
        model_status = " Ollama model unloaded."
    except Exception:
        model_status = " Ollama was not reachable; wake shutdown was still requested."
    notify("Voice Agent", "Going to sleep.")
    print(f"Wake session stop requested.{model_status}")
    return 0


def _wake(args) -> int:
    config = load_config()
    config["llm"] = dict(config.get("llm", {}))
    config["llm"]["keep_alive"] = "30m"
    config["llm"]["num_predict"] = min(int(config["llm"].get("num_predict", 192)), 192)
    config["tts"] = dict(config.get("tts", {}))
    config["tts"]["max_chars"] = min(int(config["tts"].get("max_chars", 350)), 350)
    agent = Agent(config, cwd=Path(args.cwd) if args.cwd else None, model=args.model, notifier=notify)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(WAKE_LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
    except FileExistsError:
        print("A wake session is already running.", file=sys.stderr)
        return 1
    WAKE_STOP_PATH.unlink(missing_ok=True)
    stop_requested = WAKE_STOP_PATH.exists
    stop_phrases = {"stop", "goodbye", "exit", "quit", "go to sleep", "thats all", "that's all"}
    session_stopped = False
    try:
        greeting = "I am awake. What would you like me to do?"
        notify("Voice Agent", greeting)
        if not tts.speak(greeting, config, force=True, stop_requested=stop_requested):
            if not stop_requested():
                print(f"Assistant: {greeting}")
        while True:
            if stop_requested():
                session_stopped = True
                break
            wav = record_utterance(
                config,
                silence_ms=int(cfg_get(config, "audio.wake_silence_ms", 1200)),
                beeps=False,
                stop_requested=stop_requested,
            )
            if stop_requested():
                session_stopped = True
                break
            if wav is None:
                continue
            try:
                transcript = stt.transcribe(wav, config).strip()
            finally:
                wav.unlink(missing_ok=True)
            if not transcript:
                print("No transcript.", file=sys.stderr)
                continue
            print(f"You: {transcript}")
            if transcript.casefold().strip().rstrip(".!?") in stop_phrases:
                session_stopped = True
                break
            response = agent.run(transcript)
            notify("Voice Agent", response[:400])
            if not tts.speak(response, config, stop_requested=stop_requested) and not stop_requested():
                print(f"Assistant: {response}")
    finally:
        WAKE_LOCK_PATH.unlink(missing_ok=True)
        WAKE_STOP_PATH.unlink(missing_ok=True)
    if not session_stopped:
        goodbye = "Okay, I am going to sleep."
        if not tts.speak(goodbye, config, force=True):
            print(f"Assistant: {goodbye}")
    return 0


def _ask(args) -> int:
    config = load_config()
    agent = Agent(config, cwd=Path(args.cwd) if args.cwd else None, model=args.model, notifier=notify)
    response = agent.run(args.text)
    if args.speak:
        notify("Voice Agent", response[:400])
        if not tts.speak(response, config):
            print(f"Assistant: {response}")
    else:
        print(f"Assistant: {response}")
    return 0


def _say(args) -> int:
    config = load_config()
    if not tts.speak(args.text, config, force=True):
        print("TTS failed.", file=sys.stderr)
        return 1
    return 0


def _transcribe(args) -> int:
    config = load_config()
    path = Path(args.file)
    if not path.is_file():
        print(f"Not a file: {path}", file=sys.stderr)
        return 1
    print(stt.transcribe(path, config))
    return 0


def _record(args) -> int:
    config = load_config()
    path = record_fixed(config, args.seconds)
    print(path)
    return 0


def _tools(args) -> int:
    config = load_config()
    _, registry, _ = build_registry(config, AuditLog(), Path.cwd())
    for name in sorted(registry):
        tool = registry[name]
        marker = "confirmation" if tool.dangerous else "safe"
        print(f"{name:18} [{marker}]  {tool.description}")
    return 0


def _audit(args) -> int:
    records = AuditLog().tail(args.count)
    if not records:
        print("No audit records.")
        return 0
    for record in records:
        fields = " ".join(f"{key}={value}" for key, value in record.items() if key not in {"ts", "event"})
        print(f"{record.get('ts', '')} {record.get('event', ''):22} {fields}")
    return 0


def _config(args) -> int:
    config = load_config()
    print(CONFIG_PATH)
    print(json.dumps(config, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="voice-agent", description="Private local voice agent with restricted system tools.")
    sub = parser.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser("doctor", help="Check local dependencies and configuration.")
    doctor.set_defaults(func=_doctor)

    init = sub.add_parser("init", help="Create the user config and data directories.")
    init.set_defaults(func=_init)

    wake = sub.add_parser("wake", help="Start a continuous voice session until stopped.")
    wake.add_argument("--cwd", help="Workspace directory for file and code tools.")
    wake.add_argument("--model", help="Override the Ollama model.")
    wake.set_defaults(func=_wake)

    stop = sub.add_parser("stop", help="Stop the active continuous wake session.")
    stop.set_defaults(func=_stop)

    ask = sub.add_parser("ask", help="Send text to the agent without using the microphone.")
    ask.add_argument("text")
    ask.add_argument("--cwd")
    ask.add_argument("--model")
    ask.add_argument("--speak", action="store_true", help="Speak the answer.")
    ask.set_defaults(func=_ask)

    say = sub.add_parser("say", help="Test offline text-to-speech.")
    say.add_argument("text")
    say.set_defaults(func=_say)

    transcribe = sub.add_parser("transcribe", help="Transcribe an existing WAV file.")
    transcribe.add_argument("file")
    transcribe.set_defaults(func=_transcribe)

    record = sub.add_parser("record", help="Record a fixed-length WAV for testing.")
    record.add_argument("--seconds", type=float, default=3.0)
    record.set_defaults(func=_record)

    tools = sub.add_parser("tools", help="List the fixed tool registry.")
    tools.set_defaults(func=_tools)

    audit = sub.add_parser("audit", help="Show recent audit records.")
    audit.add_argument("-n", "--count", type=int, default=20)
    audit.set_defaults(func=_audit)

    config = sub.add_parser("config", help="Print the effective configuration.")
    config.set_defaults(func=_config)

    desktop = sub.add_parser("install-desktop", help="Install a user desktop entry and print KDE shortcut steps.")
    desktop.set_defaults(func=_install_desktop)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"voice-agent: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
