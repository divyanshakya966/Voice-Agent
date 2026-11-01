# Configuration

The user configuration is stored at:

```text
~/.config/voice-agent/config.json
```

The file is created with mode `0600`. Defaults are merged with user values, so a partial file is valid. Print the effective merged configuration with:

```bash
./bin/voice-agent config
```

## Example

The paths below use XDG defaults. They are examples, not values copied from a particular machine.

```json
{
  "paths": {
    "whisper_cli": "~/.local/share/voice-agent/bin/whisper-cli",
    "whisper_model": "~/.local/share/voice-agent/models/ggml-base.en.bin",
    "piper": "~/.local/share/voice-agent/piper/piper",
    "piper_voice": "~/.local/share/voice-agent/models/en_US-lessac-medium.onnx"
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
    "silence_ms": 5000,
    "wake_silence_ms": 900,
    "max_seconds": 20,
    "start_timeout": 6,
    "barge_in": false
  },
  "stt": {
    "language": "en",
    "threads": 8,
    "timeout": 120,
    "confirm_transcript": false,
    "confirmation_retries": 1,
    "confirmation_max_chars": 180
  },
  "tts": {
    "enabled": true,
    "engine": "piper",
    "fallback": "espeak-ng",
    "max_chars": 350,
    "timeout": 60,
    "status_updates": true,
    "acknowledgement": true
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
    "thinking": false
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
      "~/Music"
    ],
    "write_roots": [
      "~/GitHub"
    ],
    "sandbox_network": false,
    "command_timeout": 90,
    "command_output_limit": 20000,
    "apps": {
      "browser": ["firefox"],
      "files": ["dolphin"],
      "elisa": ["elisa"],
      "terminal": ["konsole"],
      "settings": ["systemsettings"]
    },
    "media_player": "elisa",
    "music_roots": ["~/Music"],
    "services": {},
    "power_actions": ["suspend", "hibernate", "poweroff", "reboot"],
    "plugin_modules": [],
    "browser": {
      "enabled": false,
      "engine": "chromium",
      "headless": true,
      "allowed_domains": [],
      "profile_dir": "~/.local/share/voice-agent/browser-profile"
    }
  }
}
```

## `paths`

| Key | Meaning |
| --- | --- |
| `whisper_cli` | Path to the whisper.cpp command-line binary |
| `whisper_model` | Path to the GGML Whisper model |
| `piper` | Path to the Piper binary |
| `piper_voice` | Path to the Piper ONNX voice model |

If a configured executable is missing, the code can fall back to a binary on `PATH` with the expected name.

## `audio`

| Key | Default | Meaning |
| --- | --- | --- |
| `source` | empty | PipeWire/PulseAudio source name. Empty uses the system default source |
| `backend` | `parec` | Capture backend: `parec` or optional streaming `sounddevice` |
| `rate` | `16000` | Capture sample rate in Hz |
| `chunk_ms` | `50` | VAD chunk size in milliseconds |
| `calibration_ms` | `350` | Ambient-noise calibration window |
| `min_rms` | `180` | Minimum speech threshold |
| `noise_multiplier` | `2.8` | Multiplier applied to the measured noise floor |
| `speech_chunks` | `3` | Consecutive chunks above threshold before speech starts; used by both backends |
| `silence_ms` | `4500` | Silence duration that ends a general utterance after speech is detected |
| `no_speech_timeout_ms` | `5000` | Maximum wait for speech after ambient-noise calibration |
| `wake_silence_ms` | `900` | Silence duration that ends each continuous wake turn |
| `max_seconds` | `20` | Maximum speech duration after detection |
| `start_timeout` | `6` | Maximum wait for speech to begin |
| `barge_in` | `false` | Listen for speech during Jarvis playback and interrupt it; requires careful echo/noise tuning |

List sources with `pactl list short sources` and set `audio.source` when the default microphone is wrong. If the agent cuts off too early, raise `silence_ms`. If background noise triggers it, raise `noise_multiplier` or `min_rms`.

For optional streaming capture, install `requirements-streaming.txt` and the PortAudio system library required by your distribution, set `audio.backend` to `sounddevice`, and point `audio.source` at a PipeWire echo-cancel virtual source. This streams audio frames through WebRTC VAD before the existing local Whisper transcription step; it does not yet stream Whisper tokens.

For browser automation, install `requirements-browser.txt` and download the selected Playwright engines with `.venv/bin/playwright install chromium firefox`. Browser automation remains disabled until `agent.browser.enabled` is set to `true` and at least one trusted HTTPS domain is added to `agent.browser.allowed_domains`. The optional browser tools support opening a page and one interaction action per call: `click`, `type`, `press`, `select`, `hover`, or `wait`. They do not upload, download, run scripts, or use the normal browser profile.

## `stt`

| Key | Default | Meaning |
| --- | --- | --- |
| `language` | `en` | Whisper language hint |
| `threads` | `8` | CPU threads for whisper.cpp |
| `timeout` | `120` | Transcription timeout in seconds |
| `confirm_transcript` | `false` | Read the recognized request aloud and require a yes/no confirmation before sending it to the model; set to `true` to opt in |
| `confirmation_retries` | `1` | Extra attempts when the confirmation answer is unclear |
| `confirmation_max_chars` | `180` | Maximum transcript characters included in the spoken readback |

## `tts`

| Key | Default | Meaning |
| --- | --- | --- |
| `enabled` | `true` | Speak answers |
| `engine` | `piper` | Primary TTS engine |
| `fallback` | `espeak-ng` | Fallback engine |
| `max_chars` | `350` | Maximum spoken text length before truncation |
| `timeout` | `60` | TTS subprocess timeout |
| `status_updates` | `true` | Speak short progress cues in wake mode, including when Jarvis is listening, acknowledging a request, and working on it |
| `acknowledgement` | `true` | Speak a short acknowledgement immediately after capture while transcription runs |

Spoken text removes Markdown, code fences, URLs, and some formatting. Long responses are truncated at a sentence boundary when possible.

`engine` selects the primary backend and `fallback` is tried only if the primary is unavailable. Set `engine` to `piper` or an `espeak-ng` executable name. Set either value to `none` or `disabled` to skip that stage; set `tts.enabled` to `false` to disable speech entirely. The engine setting is honored at runtime, not just documented.

When `status_updates` is enabled, wake sessions speak when they are listening, after capture completes, before the first safe tool runs, when no transcript was recognized, and when the local model is still warming up. Voice confirmations for dangerous tools are controlled by `agent.confirmation` and always speak their prompt. Transcript readback confirmation is separate and disabled by default; set `stt.confirm_transcript` to `true` to enable it. Set `status_updates` to `false` to keep spoken answers but suppress progress cues.

The post-transcription acknowledgement is intentionally brief (`Okay.`) and runs asynchronously while model processing starts. Set `tts.acknowledgement` to `false` if you want to remove this cue entirely; it remains serialized with tool-progress and final-response audio.

Wake-session audit records include the effective audio backend/source, STT language, transcript text, confirmation result, and an `acknowledgement_start` timing event. This makes it possible to distinguish microphone capture, transcription, acknowledgement, and model latency with `voice-agent audit`.

Jarvis uses the locally configured Piper voice. To use another legally obtained Piper voice, set `PIPER_VOICE` during setup or point `paths.piper_voice` to its local model file.

## `llm`

| Key | Default | Meaning |
| --- | --- | --- |
| `url` | `http://127.0.0.1:11434` | Ollama API endpoint |
| `model` | `qwen3:1.7b` | Default model |
| `keep_alive` | `30m` | How long Ollama keeps the model loaded |
| `num_ctx` | `4096` | Context window |
| `num_predict` | `192` | Maximum generated tokens per turn |
| `temperature` | `0.2` | Sampling temperature |
| `timeout` | `180` | HTTP timeout in seconds |
| `max_steps` | `6` | Maximum model/tool turns |
| `thinking` | `false` | Request thinking mode from models that support it |

`keep_alive` controls idle memory use. Increase it if you want faster follow-up commands at the cost of keeping the model resident. Lower values free memory sooner but make the next command pay the model load cost.

Changing `llm.url` to a non-local address sends transcripts and tool results to that endpoint. Do that only if you trust the endpoint and its transport security.

## `agent`

| Key | Default | Meaning |
| --- | --- | --- |
| `confirmation` | `gui` | Confirmation channel: `gui`, `voice`, `terminal`, or `auto` |
| `session_history_messages` | `24` | Maximum prior conversation messages retained during one wake session |
| `session_history_chars` | `12000` | Approximate character budget for prior turns; older large tool results are dropped first |
| `allowed_read_roots` | See example | Filesystem roots available to read tools |
| `write_roots` | See example | Filesystem roots available to write tools |
| `sandbox_network` | `false` | Allow network access in `run_check` |
| `command_timeout` | `90` | Sandboxed command timeout |
| `command_output_limit` | `20000` | Maximum command output returned |
| `apps` | Built-in KDE defaults | Spoken app name to argv mapping |
| `media_player` | `elisa` | App name used when a play request needs to start a media player |
| `music_roots` | `["~/Music"]` | Directories searched for local audio tracks |
| `services` | `{}` | Spoken service name to a user-systemd unit; system services are never controlled |
| `power_actions` | suspend, hibernate, poweroff, reboot | Explicitly permitted power actions; all require confirmation |
| `plugin_modules` | `[]` | Explicit Python plugin modules trusted to expose `build_tools(context)` |
| `browser` | disabled | Optional Playwright browser capability with HTTPS domain allowlisting |

### Plugin modules

Plugins are trusted user-level Python code, not a sandbox boundary. Add a module name to `agent.plugin_modules` only when its source and dependencies are trusted. A plugin must expose `build_tools(context)` and return `Tool` objects; every returned tool still passes through Jarvis's argument validation, confirmation, audit, and registry checks.

Local personal tools provide reminders and email drafts. Reminders use a locked `~/.local/state/voice-agent/reminders.json` and notify during an active wake session or through `voice-agent reminders watch`. Email drafts support `create`, `list`, `show`, and `delete`; they are saved as mode-`0600` `.eml` files and are never sent automatically. Provider sync is not enabled.

### Optional supervised service

The repository includes opt-in user services at `systemd/voice-agent-wake.service` and `systemd/voice-agent-reminders.service`. Install either or both with:

```bash
mkdir -p ~/.config/systemd/user
cp systemd/voice-agent-wake.service systemd/voice-agent-reminders.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now voice-agent-wake.service
systemctl --user enable --now voice-agent-reminders.service
```

The wake service starts continuous wake mode after login and restarts it after failures. The reminder service polls local reminder state every 30 seconds and delivers due notifications without capturing audio or using the network. Both units apply kernel, filesystem, privilege, and address-family restrictions while retaining the user-session access they need. They are still same-user processes, not VM or privilege boundaries. Do not enable wake if you want the microphone active only after the Meta+Alt shortcut; `systemctl --user disable --now voice-agent-wake.service` returns to explicit wake/stop control.

### Confirmation modes

- `gui`: use `kdialog` or `zenity`, then fall back to a terminal prompt.
- `voice`: ask aloud and transcribe a yes/no answer, then fall back to a dialog.
- `terminal`: read `y`/`yes` from the current terminal.
- `auto`: choose a GUI dialog automatically when available, otherwise use the terminal.

`auto` does not approve actions automatically. If no confirmation channel is available, the action is denied.

### App allowlist

`open_app` only accepts the keys in `agent.apps`. Values are argument arrays, not shell strings:

```json
{
  "editor": ["kate"],
  "browser": ["firefox"]
}
```

## Environment variables

Runtime paths honor the standard XDG variables:

- `XDG_CONFIG_HOME`
- `XDG_DATA_HOME`
- `XDG_STATE_HOME`
- `XDG_CACHE_HOME`

The setup script also accepts:

- `WHISPER_MODEL` (default `base.en`)
- `WHISPER_TAG` (default `v1.8.1`)
- `PIPER_VERSION` (default `2023.11.14-2`)
- `PIPER_VOICE` (default `en_US-lessac-medium`)

The launcher sets `PYTHONPATH` to the repository's `src` directory. It does not require a virtual environment.
