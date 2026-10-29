# Aegis Voice Agent

A private, local-first push-to-talk voice assistant for Linux desktops. Speech recognition, language inference, and text-to-speech run on your machine. The assistant can inspect system status, control a small set of desktop actions, read and search files, run allowlisted development checks in a sandbox, and perform read-only security scans through a fixed tool registry.

This is a working prototype, not a general-purpose autonomous agent. It has no always-on daemon, no cloud dependency, and no arbitrary shell tool.

## Highlights

- Offline speech-to-text with [whisper.cpp](https://github.com/ggerganov/whisper.cpp)
- Offline text-to-speech with [Piper](https://github.com/rhasspy/piper), with `espeak-ng` fallback
- Local language model through [Ollama](https://ollama.com)
- Push-to-talk, one command per invocation
- Fixed tool allowlist instead of open-ended shell access
- GUI, voice, or terminal confirmation for writes, commands, and clipboard reads
- Bubblewrap sandbox for development checks, network disabled by default
- Audit log with best-effort secret redaction
- Model unloads after 10 seconds of inactivity by default
- No telemetry

## Requirements

- Linux with PipeWire or PulseAudio (`parec`, and `paplay`/`pw-play`/`aplay`)
- Python 3.10 or newer
- Ollama running on `127.0.0.1:11434`
- KDE Plasma is the primary desktop target (`kdialog`, `wpctl`, `spectacle`, `wl-clipboard`)
- `bubblewrap` for sandboxed development checks
- Build tools for the first setup: `curl`, `git`, `cmake`, `g++`, `make`

Optional components are reported by `doctor`. Missing optional tools only disable the related feature.

## Quick start

```bash
git clone <repo-url> voice-agent
cd voice-agent

# Build whisper.cpp, download the STT model and Piper voice,
# create the config, and link the launcher into ~/.local/bin.
./scripts/setup.sh

# Pull the default fast model. The setup script does not pull Ollama models.
ollama pull qwen3:1.7b

./bin/voice-agent doctor
./bin/voice-agent ask "What is the current volume?"
./bin/voice-agent listen --notify
```

If `~/.local/bin` is not on your `PATH`, use `./bin/voice-agent` or the absolute path printed by the installer.

## Push-to-talk

Install the desktop entry and bind a shortcut:

```bash
./bin/voice-agent install-desktop
```

Then open **System Settings → Keyboard → Shortcuts → Custom Shortcuts**, create a command shortcut, and use the command printed by the installer:

```bash
/path/to/voice-agent/bin/voice-agent listen --notify
```

`Meta+Space` is a suggested binding. The desktop entry itself does not force a global shortcut.

## Models

The default is chosen for low latency and a permissive license. You can override it per command with `--model`.

| Use case | Model | License |
| --- | --- | --- |
| Fast default | `qwen3:1.7b` | Apache-2.0 |
| Harder coding and reasoning | `qwen2.5-coder:7b` | Apache-2.0 |

```bash
./bin/voice-agent ask --model qwen2.5-coder:7b "Review this function and suggest a safer implementation"
```

Model licenses and behavior are controlled by their upstream projects. Some Qwen variants use different licenses; verify before redistributing a model.

## Commands

| Command | Purpose |
| --- | --- |
| `voice-agent doctor` | Check dependencies, models, audio, GPU, sandbox, and desktop tools |
| `voice-agent init` | Create the user config and data directories |
| `voice-agent listen` | Record one spoken command, run it, and speak the answer |
| `voice-agent ask "..."` | Send text to the agent without using the microphone |
| `voice-agent say "..."` | Test offline text-to-speech |
| `voice-agent transcribe file.wav` | Transcribe an existing WAV file |
| `voice-agent record --seconds 3` | Record a fixed-length WAV for testing |
| `voice-agent tools` | List the fixed tool registry |
| `voice-agent audit -n 20` | Show recent local audit records |
| `voice-agent config` | Print the effective configuration |
| `voice-agent install-desktop` | Install a user desktop entry and print PTT setup steps |

Useful `listen` options include `--cwd`, `--model`, `--notify`, `--no-tts`, `--start-timeout`, `--max-seconds`, and `--silence-ms`.

## Security model

The agent is deliberately restricted:

- The model can only call tools that exist in the local registry.
- File reads and writes are limited to configured roots.
- Secret files, keyrings, browser profiles, SSH keys, and similar paths are blocked.
- Writes, patches, development checks, and clipboard reads require confirmation by default.
- Development checks run inside a bubblewrap sandbox with no network, a read-only system, and only the workspace mounted read-write.
- The sandbox is a containment layer, not a virtual machine.
- Audit records are local and may contain transcripts or command output. Redaction is best effort, not a guarantee.

See [SECURITY.md](SECURITY.md) for the full model, limitations, and reporting guidance.

## Data and privacy

No telemetry is sent anywhere. Runtime inference goes to the configured Ollama endpoint, which defaults to localhost. The setup script downloads models and binaries from their upstream release hosts.

Default locations follow the XDG base directory specification:

| Data | Location |
| --- | --- |
| Configuration | `~/.config/voice-agent/config.json` |
| Binaries and models | `~/.local/share/voice-agent/` |
| Audit log | `~/.local/state/voice-agent/audit.jsonl` |
| File backups | `~/.local/state/voice-agent/backups/` |
| Temporary audio | `~/.cache/voice-agent/` and the system temp directory |

See [docs/PRIVACY.md](docs/PRIVACY.md) for details and deletion instructions.

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Configuration](docs/CONFIGURATION.md)
- [Tools](docs/TOOLS.md)
- [Privacy](docs/PRIVACY.md)
- [Troubleshooting](docs/TROUBLESHOOTING.md)
- [Security policy](SECURITY.md)
- [Contributing](CONTRIBUTING.md)

## Known limitations

- Primary support is Linux with KDE Plasma and PipeWire; other desktops may need configuration changes.
- There is no wake word. Activation is push-to-talk or an explicit command.
- One invocation handles one command. Follow-up context is not retained between invocations.
- Small local models can make mistakes, ignore style instructions, or choose an unexpected tool. Confirmation and tool validation reduce the impact, but do not eliminate it.
- The sandboxed checks still execute project code when you approve `pytest`, `npm test`, or similar commands. Treat any workspace you scan as potentially untrusted.
- The audit log is not encrypted.

## License

No license has been selected for this repository yet. Add a `LICENSE` file before publishing or distributing it.
