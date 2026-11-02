from __future__ import annotations

import argparse
import json
import math
import os
import signal
import stat
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

from . import __version__, llm, stt, tts
from .agent import Agent, _yes_no
from .audio import AudioError, record_fixed, record_utterance
from .audit import AuditLog
from .config import (
    CONFIG_PATH,
    DATA_DIR,
    STATE_DIR,
    cfg_get,
    expand_path,
    load_config,
    save_config,
)
from .sandbox import available as bwrap_available
from .sandbox import run_sandboxed
from .tools import build_registry
from .tools.personal import (
    cancel_reminder,
    create_reminder,
    due_reminders,
    list_reminders,
)
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
    try:
        mode = stat.S_IMODE(CONFIG_PATH.stat().st_mode)
        _ok("config permissions", not bool(mode & 0o077), oct(mode))
    except OSError as exc:
        _ok("config permissions", False, str(exc))

    whisper = stt.whisper_binary(config)
    critical_ok &= _ok("whisper-cli", bool(whisper), whisper or "missing", critical=True)
    model = stt.whisper_model(config)
    critical_ok &= _ok("whisper model", model.is_file(), str(model), critical=True)
    piper = expand_path(str(cfg_get(config, "paths.piper", "")))
    voice = expand_path(str(cfg_get(config, "paths.piper_voice", "")))
    tts_engine = str(cfg_get(config, "tts.engine", "piper")).strip().lower()
    tts_fallback = str(cfg_get(config, "tts.fallback", "espeak-ng")).strip().lower()
    if not bool(cfg_get(config, "tts.enabled", True)):
        _ok("tts", True, "disabled")
    else:
        if "piper" in {tts_engine, tts_fallback}:
            _ok("piper binary", piper.is_file(), str(piper))
            _ok("piper voice", voice.is_file(), str(voice))
        for label, name in (("tts engine", tts_engine), ("tts fallback", tts_fallback)):
            if name not in {"", "none", "disabled", "piper"}:
                _ok(label, bool(which(name)), name)

    try:
        ollama_version = llm.version(config)
        _ok("ollama", True, ollama_version, critical=True)
        models = {item.get("name", "") for item in llm.tags(config)}
        configured = str(cfg_get(config, "llm.model", "qwen3:1.7b"))
        _ok(f"llm model {configured}", configured in models, "not in ollama list" if configured not in models else "")
    except Exception as exc:
        critical_ok = False
        print(f"[FAIL] ollama  {exc}")

    try:
        gpu = run(["nvidia-smi", "--query-gpu=name,memory.total,memory.used", "--format=csv,noheader"], timeout=10)
        _ok("nvidia gpu", gpu.returncode == 0, gpu.stdout.strip() or gpu.stderr.strip())
    except OSError as exc:
        _ok("nvidia gpu", False, str(exc))

    backend = str(cfg_get(config, "audio.backend", "parec")).lower()
    if backend == "sounddevice":
        try:
            import sounddevice  # noqa: F401
            import webrtcvad  # noqa: F401
            _ok("sounddevice backend", True, "streaming capture available", critical=True)
        except (ImportError, OSError) as exc:
            critical_ok &= _ok("sounddevice backend", False, f"install requirements-streaming.txt ({exc})", critical=True)
    elif backend == "parec":
        critical_ok &= _ok("parec", bool(which("parec")), "", critical=True)
    else:
        critical_ok &= _ok("audio backend", False, f"unsupported backend: {backend}", critical=True)
    critical_ok &= _ok(
        "audio playback",
        any(which(name) for name in ("paplay", "pw-play", "aplay")),
        "",
        critical=True,
    )
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
                critical_ok &= _ok(
                    "sandbox test",
                    code == 0 and "sandbox-ok" in output,
                    output.strip(),
                    critical=True,
                )
            except Exception as exc:
                critical_ok &= _ok("sandbox test", False, str(exc), critical=True)
        else:
            critical_ok &= _ok("sandbox test", False, "no write root exists", critical=True)

    write_roots = [expand_path(value) for value in cfg_get(config, "agent.write_roots", [])]
    _ok("write roots", any(root.is_dir() for root in write_roots), ", ".join(str(root) for root in write_roots))
    try:
        build_registry(config, AuditLog(), Path.cwd())
        _ok("tool registry", True, "plugins and tools loaded")
    except Exception as exc:
        critical_ok &= _ok("tool registry", False, str(exc), critical=True)
    if bool(cfg_get(config, "agent.browser.enabled", False)):
        try:
            import playwright.sync_api  # noqa: F401
            _ok("browser support", True, "playwright available")
        except (ImportError, OSError) as exc:
            _ok("browser support", False, f"install requirements-browser.txt ({exc})")
        _ok(
            "browser domains",
            bool(cfg_get(config, "agent.browser.allowed_domains", [])),
            "add at least one HTTPS domain",
        )
    try:
        list_reminders()
        _ok("reminder storage", True, "readable")
    except Exception as exc:
        _ok("reminder storage", False, str(exc))

    _ok("kdialog", bool(which("kdialog")), "GUI confirmations")
    _ok("spectacle", bool(which("spectacle")), "screenshots")
    _ok("wpctl", bool(which("wpctl")), "volume")
    _ok("wl-clipboard", bool(which("wl-copy") and which("wl-paste")), "clipboard")
    _ok("playerctl", bool(which("playerctl")), "optional; MPRIS fallback is used")
    _ok("desktop notifications", bool(which("notify-send") or which("kdialog")), "notify-send or kdialog")

    print("\nStatus:", "ready" if critical_ok else "needs attention")
    print("Tip: set agent.confirmation to gui, voice, terminal, or auto in config.json.")
    return 0 if critical_ok else 1


def _version(args) -> int:
    print(__version__)
    return 0


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
Name=Jarvis
Comment=Continuous local Jarvis voice assistant
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


def _wake_process(pid: int) -> bool:
    try:
        command = Path(f"/proc/{pid}/cmdline").read_bytes().decode(errors="replace").replace("\x00", " ")
    except (OSError, UnicodeError):
        return False
    return "voice_agent" in command and " wake" in f" {command}"


def _create_wake_lock() -> None:
    fd = os.open(WAKE_LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        os.write(fd, str(os.getpid()).encode("ascii"))
    finally:
        os.close(fd)


def _stop(args) -> int:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    atomic_write(WAKE_STOP_PATH, "stop\n", mode=0o600)
    try:
        pid = int(WAKE_LOCK_PATH.read_text(encoding="ascii").strip())
    except (FileNotFoundError, OSError, ValueError):
        pid = 0
    if pid and pid != os.getpid() and _wake_process(pid):
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    config = load_config()
    try:
        llm.unload(config)
        model_status = " Ollama model unloaded."
    except Exception:
        model_status = " Ollama was not reachable; wake shutdown was still requested."
    notify("Jarvis", "Going to sleep.")
    print(f"Wake session stop requested.{model_status}")
    return 0


def _deliver_due_reminders(audit: AuditLog, *, echo: bool = True) -> int:
    delivered = 0
    for reminder in due_reminders():
        notify("Jarvis reminder", reminder[:400])
        audit.write("reminder_notified", text=reminder[:500])
        if echo:
            print(f"Reminder: {reminder}", file=sys.stderr, flush=True)
        delivered += 1
    return delivered


def _speak_status(
    config: dict,
    message: str,
    stop_requested: Callable[[], bool] | None = None,
) -> bool:
    if not bool(cfg_get(config, "tts.status_updates", True)):
        return False
    if stop_requested and stop_requested():
        return False
    try:
        return tts.speak(message, config, stop_requested=stop_requested)
    except Exception as exc:
        print(f"Status update failed: {exc}", file=sys.stderr, flush=True)
        return False


def _speak_status_background(
    config: dict,
    message: str,
    stop_requested: Callable[[], bool] | None = None,
) -> threading.Thread | None:
    if not bool(cfg_get(config, "tts.status_updates", True)) or not bool(
        cfg_get(config, "tts.acknowledgement", False)
    ):
        return None
    thread = threading.Thread(
        target=_speak_status,
        args=(config, message, stop_requested),
        name="jarvis-status-speech",
        daemon=True,
    )
    thread.start()
    return thread


def _confirm_transcript(
    config: dict,
    transcript: str,
    stop_requested: Callable[[], bool] | None = None,
) -> bool | None:
    if not bool(cfg_get(config, "stt.confirm_transcript", False)):
        return True
    if stop_requested and stop_requested():
        return None
    max_chars = max(20, int(cfg_get(config, "stt.confirmation_max_chars", 180)))
    readback = transcript[:max_chars].rstrip()
    if len(transcript) > max_chars:
        readback = f"{readback} ..."
    prompt = f'I heard: "{readback}". Is that correct? Say yes or no.'
    retries = max(0, int(cfg_get(config, "stt.confirmation_retries", 1)))
    for attempt in range(retries + 1):
        try:
            message = prompt if attempt == 0 else "Please say yes or no."
            if not tts.speak(message, config, force=True, stop_requested=stop_requested):
                return None
            wav = record_utterance(
                config,
                start_timeout=4,
                max_seconds=4,
                silence_ms=700,
                stop_requested=stop_requested,
            )
            if wav is None:
                return None
            try:
                answer = stt.transcribe(wav, config).strip()
            finally:
                wav.unlink(missing_ok=True)
        except (AudioError, stt.STTError):
            return None
        decision = _yes_no(answer)
        if decision == "yes":
            return True
        if decision == "no":
            return False
    return None


def _reminders(args) -> int:
    action = args.action
    if action == "list":
        print(list_reminders())
        return 0
    if action == "create":
        if not args.text or not args.due:
            print("reminders create requires --text and --due.", file=sys.stderr)
            return 2
        print(create_reminder(args.text, args.due))
        return 0
    if action == "cancel":
        if not args.id:
            print("reminders cancel requires --id.", file=sys.stderr)
            return 2
        print(cancel_reminder(args.id))
        return 0
    interval = max(1.0, float(args.interval))
    audit = AuditLog()
    stop = threading.Event()

    def request_stop(signum, frame) -> None:
        stop.set()

    previous_term = signal.signal(signal.SIGTERM, request_stop)
    previous_int = signal.signal(signal.SIGINT, request_stop)
    print(f"Watching reminders every {interval:g}s. Press Ctrl+C to stop.", file=sys.stderr)
    try:
        while not stop.is_set():
            try:
                _deliver_due_reminders(audit)
            except Exception as exc:
                audit.write("reminder_error", error=repr(exc))
                print(f"Reminder check failed: {exc}", file=sys.stderr)
            if stop.wait(interval):
                break
    finally:
        signal.signal(signal.SIGTERM, previous_term)
        signal.signal(signal.SIGINT, previous_int)
    return 0


def _wake(args) -> int:
    config = load_config()
    config["llm"] = dict(config.get("llm", {}))
    config["llm"]["keep_alive"] = "30m"
    config["llm"]["num_predict"] = min(int(config["llm"].get("num_predict", 192)), 192)
    config["tts"] = dict(config.get("tts", {}))
    config["tts"]["max_chars"] = min(int(config["tts"].get("max_chars", 350)), 350)
    status_turn = {"tool": False}
    acknowledgement_thread: threading.Thread | None = None

    def join_acknowledgement() -> None:
        nonlocal acknowledgement_thread
        if acknowledgement_thread is None:
            return
        started = acknowledgement_started
        acknowledgement_thread.join()
        if started is not None:
            agent.audit.write(
                "timing",
                phase="acknowledgement",
                seconds=round(time.monotonic() - started, 3),
            )
        acknowledgement_thread = None

    def report_tool_status(_name: str) -> None:
        if status_turn["tool"]:
            return
        status_turn["tool"] = True
        join_acknowledgement()
        _speak_status(config, "Checking that now.", stop_requested)

    agent = Agent(
        config,
        cwd=Path(args.cwd) if args.cwd else None,
        model=args.model,
        notifier=notify,
        status_reporter=report_tool_status,
    )
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        _create_wake_lock()
    except FileExistsError:
        try:
            pid = int(WAKE_LOCK_PATH.read_text(encoding="ascii").strip())
        except (OSError, ValueError):
            pid = 0
        if pid and _wake_process(pid):
            print("A wake session is already running.", file=sys.stderr)
            return 1
        WAKE_LOCK_PATH.unlink(missing_ok=True)
        try:
            _create_wake_lock()
        except FileExistsError:
            print("A wake session is already running.", file=sys.stderr)
            return 1
    WAKE_STOP_PATH.unlink(missing_ok=True)
    stop_requested = WAKE_STOP_PATH.exists
    stop_phrases = {"stop", "goodbye", "exit", "quit", "go to sleep", "thats all", "that's all"}
    barge_in = bool(cfg_get(config, "audio.barge_in", False))
    session_stopped = False
    warmup_error: list[Exception] = []
    warmup_duration: list[float] = []
    reminder_stop = threading.Event()

    def warm_model() -> None:
        started = time.monotonic()
        try:
            llm.warmup(config, model=agent.model)
        except Exception as exc:
            warmup_error.append(exc)
        finally:
            warmup_duration.append(round(time.monotonic() - started, 3))

    def reminder_worker() -> None:
        while not reminder_stop.is_set():
            try:
                _deliver_due_reminders(agent.audit, echo=False)
            except Exception as exc:
                agent.audit.write("reminder_error", error=repr(exc))
            if reminder_stop.wait(5):
                break

    warmup_thread = threading.Thread(target=warm_model, name="jarvis-ollama-warmup", daemon=True)
    reminder_thread = threading.Thread(target=reminder_worker, name="jarvis-reminders", daemon=True)
    warmup_thread.start()
    reminder_thread.start()
    try:
        greeting = "Jarvis is awake. What would you like me to do?"
        notify("Jarvis", greeting)
        if not tts.speak(greeting, config, force=True, stop_requested=stop_requested) and not stop_requested():
            print(f"Assistant: {greeting}")
        warmup_thread.join()
        agent.audit.write(
            "llm_warmup",
            model=agent.model,
            seconds=warmup_duration[0] if warmup_duration else None,
            success=not warmup_error,
        )
        if warmup_error:
            agent.audit.write("llm_warmup_error", error=repr(warmup_error[0]))
            warning = "Ollama is not ready; the first request may take longer."
            notify("Jarvis", warning)
            _speak_status(config, warning, stop_requested)
        while True:
            if stop_requested():
                session_stopped = True
                break
            capture_started = time.monotonic()
            wav = record_utterance(
                config,
                silence_ms=int(cfg_get(config, "audio.wake_silence_ms", 900)),
                stop_requested=stop_requested,
            )
            if stop_requested():
                session_stopped = True
                break
            if wav is None:
                continue
            agent.audit.write(
                "timing",
                phase="capture",
                seconds=round(time.monotonic() - capture_started, 3),
                backend=str(cfg_get(config, "audio.backend", "parec")),
                source=str(cfg_get(config, "audio.source", "") or "default"),
            )
            acknowledgement_thread = _speak_status_background(
                config,
                "Okay.",
                stop_requested,
            )
            acknowledgement_started = time.monotonic() if acknowledgement_thread is not None else None
            if acknowledgement_thread is not None:
                agent.audit.write("timing", phase="acknowledgement_start")
            transcription_started = time.monotonic()
            try:
                transcript = stt.transcribe(wav, config).strip()
            finally:
                wav.unlink(missing_ok=True)
            agent.audit.write(
                "timing",
                phase="transcription",
                seconds=round(time.monotonic() - transcription_started, 3),
                language=str(cfg_get(config, "stt.language", "en")),
            )
            if not transcript:
                join_acknowledgement()
                continue
            print(f"You: {transcript}")
            agent.audit.write("transcript", text=transcript)
            if transcript.casefold().strip().rstrip(".!?") in stop_phrases:
                join_acknowledgement()
                session_stopped = True
                break
            if bool(cfg_get(config, "stt.confirm_transcript", False)):
                join_acknowledgement()
            confirmation = _confirm_transcript(config, transcript, stop_requested)
            agent.audit.write("transcript_confirmation", confirmed=confirmation is True)
            if confirmation is not True:
                if confirmation is False:
                    _speak_status(config, "Please repeat your request.", stop_requested)
                else:
                    _speak_status(config, "I could not confirm that. Please repeat your request.", stop_requested)
                continue
            status_turn["tool"] = False
            reasoning_started = time.monotonic()
            response = agent.run(transcript)
            agent.audit.write("timing", phase="agent", seconds=round(time.monotonic() - reasoning_started, 3))
            join_acknowledgement()
            notify("Jarvis", response[:400])
            speech_started = time.monotonic()
            if barge_in:
                interrupted_wav: list[Path | None] = []
                capture_error: list[Exception] = []
                speech_detected = threading.Event()
                capture_stop = threading.Event()
                capture_ready = threading.Event()

                def capture_interruption(
                    wav_target=interrupted_wav,
                    error_target=capture_error,
                    stop_event=capture_stop,
                    speech_event=speech_detected,
                    ready_event=capture_ready,
                ) -> None:
                    try:
                        wav_target.append(
                            record_utterance(
                                config,
                                silence_ms=int(cfg_get(config, "audio.wake_silence_ms", 900)),
                                stop_requested=lambda: stop_requested() or stop_event.is_set(),
                                speech_started=speech_event.set,
                                capture_ready=ready_event.set,
                            )
                        )
                    except Exception as exc:
                        error_target.append(exc)
                        stop_event.set()

                capture_thread = threading.Thread(target=capture_interruption, name="jarvis-barge-in", daemon=True)
                capture_thread.start()
                capture_ready.wait(timeout=2)
                spoke = tts.speak(
                    response,
                    config,
                    stop_requested=lambda stop_check=stop_requested, speech_event=speech_detected: stop_check()
                    or speech_event.is_set(),
                )
                if not speech_detected.is_set():
                    capture_stop.set()
                capture_thread.join()
                if capture_error:
                    agent.audit.write("barge_in_error", error=repr(capture_error[0]))
                interrupted = interrupted_wav[0] if interrupted_wav else None
                if speech_detected.is_set() and interrupted:
                    try:
                        correction = stt.transcribe(interrupted, config).strip()
                    finally:
                        interrupted.unlink(missing_ok=True)
                    if correction:
                        transcript = correction
                        print(f"You: {transcript}")
                        _speak_status(config, "Got it. Working on it.", stop_requested)
                        status_turn["tool"] = False
                        response = agent.run(transcript)
                        notify("Jarvis", response[:400])
                        if not tts.speak(response, config, stop_requested=stop_requested):
                            print(f"Assistant: {response}")
                elif interrupted:
                    interrupted.unlink(missing_ok=True)
                if not spoke and not stop_requested() and not speech_detected.is_set():
                    print(f"Assistant: {response}")
            elif not tts.speak(response, config, stop_requested=stop_requested) and not stop_requested():
                print(f"Assistant: {response}")
            agent.audit.write("timing", phase="speech", seconds=round(time.monotonic() - speech_started, 3))
    finally:
        reminder_stop.set()
        reminder_thread.join(timeout=2)
        WAKE_LOCK_PATH.unlink(missing_ok=True)
        WAKE_STOP_PATH.unlink(missing_ok=True)
    if not session_stopped:
        goodbye = "Jarvis is going to sleep."
        if not tts.speak(goodbye, config, force=True):
            print(f"Assistant: {goodbye}")
    return 0


def _ask(args) -> int:
    config = load_config()
    agent = Agent(config, cwd=Path(args.cwd) if args.cwd else None, model=args.model, notifier=notify)
    response = agent.run(args.text)
    if args.speak:
        notify("Jarvis", response[:400])
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


def positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


def positive_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("must be a finite number greater than 0")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="voice-agent", description="Private local voice agent with restricted system tools.")
    sub = parser.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser("doctor", help="Check local dependencies and configuration.")
    doctor.set_defaults(func=_doctor)

    version = sub.add_parser("version", help="Print the voice-agent version.")
    version.set_defaults(func=_version)

    init = sub.add_parser("init", help="Create the user config and data directories.")
    init.set_defaults(func=_init)

    wake = sub.add_parser("wake", help="Start a continuous voice session until stopped.")
    wake.add_argument("--cwd", help="Workspace directory for file and code tools.")
    wake.add_argument("--model", help="Override the Ollama model.")
    wake.set_defaults(func=_wake)

    stop = sub.add_parser("stop", help="Stop the active continuous wake session.")
    stop.set_defaults(func=_stop)

    reminders = sub.add_parser("reminders", help="List, create, cancel, or watch local reminders.")
    reminders.add_argument("action", nargs="?", choices=["list", "create", "cancel", "watch"], default="list")
    reminders.add_argument("--text", help="Reminder text for create.")
    reminders.add_argument("--due", help="ISO-8601 due time for create.")
    reminders.add_argument("--id", help="Reminder ID for cancel.")
    reminders.add_argument("--interval", type=positive_float, default=30.0, help="Watch polling interval in seconds.")
    reminders.set_defaults(func=_reminders)

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
    record.add_argument("--seconds", type=positive_float, default=3.0)
    record.set_defaults(func=_record)

    tools = sub.add_parser("tools", help="List the fixed tool registry.")
    tools.set_defaults(func=_tools)

    audit = sub.add_parser("audit", help="Show recent audit records.")
    audit.add_argument("-n", "--count", type=positive_int, default=20)
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
