# Privacy

## Local by default

The normal pipeline is local:

- Audio is captured by `parec`.
- Speech is transcribed by `whisper-cli` on the local CPU.
- The transcript is sent to the configured Ollama endpoint. The default is `http://127.0.0.1:11434`.
- Tool calls run locally.
- The answer is spoken by Piper or `espeak-ng`.

The project does not include analytics, crash reporting, telemetry, or a cloud API client.

## Network access

The agent itself makes no network requests during a voice command unless you change the configuration. Network behavior comes from:

- The Ollama endpoint. If `llm.url` is remote, prompts and tool results leave the machine.
- `run_check` when `agent.sandbox_network` is explicitly enabled.
- The setup script, which downloads whisper.cpp, a Whisper model, Piper, and a Piper voice.

Ollama may have its own update checks or telemetry settings. Those are controlled by Ollama, not by this project. Review Ollama's configuration if that matters to you.

The setup script downloads from GitHub and Hugging Face over HTTPS. It does not verify a pinned checksum for every artifact. If you need reproducibility, pin and verify the artifacts yourself.

## Data stored locally

With XDG defaults:

| Data | Location | Notes |
| --- | --- | --- |
| Configuration | `~/.config/voice-agent/config.json` | Mode `0600` |
| Binaries and models | `~/.local/share/voice-agent/` | Downloaded or built locally |
| Audit log | `~/.local/state/voice-agent/audit.jsonl` | Mode `0600`, JSON Lines |
| File backups | `~/.local/state/voice-agent/backups/` | Created before writes and patches |
| Temporary audio | `~/.cache/voice-agent/` and the system temp directory | Deleted after transcription in the wake session |
| Screenshots | `~/Pictures/Screenshots/` | Created by the `screenshot` tool |
| Clipboard | Desktop clipboard | Changed by `clipboard_write` |

The audit log can include:
- The user transcript
- Model and tool decisions
- Tool arguments
- Tool output, truncated
- Confirmation requests and decisions
- Timing and token counts

Wake phase timings are also recorded for capture, transcription, agent/tool processing, and speech. They do not contain audio or transcript text by themselves.

Strings are passed through a best-effort redactor, but the log is not encrypted. Treat it as sensitive.

## Spoken and displayed output

- `voice-agent say` and `wake` play audio through the configured audio output.
- Terminal output may remain in shell history or terminal scrollback.
- Speech output can be audible to people nearby.

## Screenshots and clipboard

`screenshot` saves an image of the current screen. It can capture private messages, keys, or account details. The image is not read back into the model, but it remains on disk.

`clipboard_read` requires confirmation by default. `clipboard_write` replaces the clipboard contents.

## Retention

Delete the audit log:

```bash
rm -f ~/.local/state/voice-agent/audit.jsonl
```

Delete backups:

```bash
rm -rf ~/.local/state/voice-agent/backups
```

Delete all local runtime data, including downloaded models and binaries:

```bash
rm -rf ~/.local/share/voice-agent ~/.local/state/voice-agent ~/.cache/voice-agent
```

Delete the configuration:

```bash
rm -f ~/.config/voice-agent/config.json
```

If full-disk encryption is not enabled, local model files, audio, and logs may be recoverable from the disk after deletion. Use full-disk encryption if that is part of your threat model.

## Remote Ollama endpoints

If you set `llm.url` to a host other than localhost, you are responsible for:

- Authenticating the connection
- Encrypting the transport
- Understanding the remote host's logging and retention
- Ensuring you are allowed to send that data to the remote host

The default configuration does not do this.
