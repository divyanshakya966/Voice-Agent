# Troubleshooting

Start with:

```bash
./bin/voice-agent doctor
```

`doctor` separates critical failures from optional warnings. The optional pieces are `playerctl`, `kdialog`, `spectacle`, `wpctl`, `wl-clipboard`, and `notify-send`.

## `voice-agent` is not found

The setup script links the launcher into `~/.local/bin`. If that directory is not on your `PATH`, call the repository launcher directly:

```bash
./bin/voice-agent doctor
```

Or use the absolute path printed by `./bin/voice-agent install-desktop`.

## No microphone input or no speech detected

1. Check the default source:

   ```bash
   pactl info
   pactl list short sources
   ```

2. Record a test clip:

   ```bash
   ./bin/voice-agent record --seconds 3
   ```

3. Transcribe it:

   ```bash
   ./bin/voice-agent transcribe /path/from/the/record/command.wav
   ```

If the default source is wrong, set `audio.source` in the config to the source name. If quiet speech is missed, lower `audio.min_rms`. If fan or room noise triggers detection, raise `audio.noise_multiplier` or `audio.min_rms`.

The wake session waits up to `audio.start_timeout` for speech, then stops each turn after `audio.wake_silence_ms` of silence or `audio.max_seconds` of speech. The general `audio.silence_ms` value is retained for non-wake capture paths.

## The agent cuts off too early

Increase `audio.silence_ms`. The default is 5000 ms. Noisy rooms may need a larger value.

## The agent records too much silence

Lower `audio.silence_ms`, raise `audio.noise_multiplier`, or use a better microphone source. Check whether the selected source is a monitor or loopback device rather than a physical microphone.

## `whisper-cli` or the Whisper model is missing

Run:

```bash
./scripts/setup.sh
./bin/voice-agent doctor
```

The setup script builds whisper.cpp and downloads `ggml-base.en.bin` unless those files already exist. Building requires `git`, `cmake`, `g++`, and `make`.

## Piper or the voice model is missing

Run `./scripts/setup.sh` again. It downloads the Piper release and the configured voice model. If Piper fails, the assistant can still use `espeak-ng` when installed.

## Text-to-speech is silent

Test the TTS path directly:

```bash
./bin/voice-agent say "Voice agent test"
```

Check the output device and volume with `wpctl status`, `pactl info`, or your desktop audio settings. The `doctor` command reports whether audio playback tools are present.

## The Ollama model is missing

```bash
ollama list
ollama pull qwen3:1.7b
./bin/voice-agent doctor
```

The setup script does not pull Ollama models.

## First response is slow

The first request after the model has been unloaded pays the model load cost. The default `llm.keep_alive` is `30m` during normal configuration, and an active `wake` session forces the same warm profile. Increase it only if you prefer faster follow-ups after very long idle periods:

```json
{
  "llm": {
    "keep_alive": "2m"
  }
}
```

This keeps the model in memory longer, which uses RAM or VRAM.

## Responses are poor or tool calls are unreliable

Small models vary in tool-calling quality. Try the larger coding model:

```bash
./bin/voice-agent ask --model qwen2.5-coder:7b "Your request"
```

Keep the request short and explicit. Check `./bin/voice-agent audit -n 20` to see which tools were selected and what they returned.

## The model appears to be thinking out loud

The default model is configured with `llm.thinking: false`. Some models ignore that setting or have templates that force thinking. If you switch models and see reasoning text, use a model that supports disabling thinking, or keep the default.

## Confirmation dialogs do not appear

The `gui` mode uses `kdialog`, then `zenity`. Without a graphical session or either tool, it falls back to a terminal prompt. From a desktop shortcut there may be no terminal, so the action is denied.

Options:

- Install `kdialog`.
- Set `agent.confirmation` to `voice`.
- Run the command from a terminal and use `agent.confirmation: terminal`.

`auto` selects a dialog when possible; it does not approve automatically.

## Sandboxed checks fail

Check:

```bash
bwrap --version
./bin/voice-agent doctor
```

The sandbox requires bubblewrap and unprivileged user namespaces. If the sandbox test fails, command execution is disabled rather than run without containment. Do not “fix” this by bypassing the sandbox.

## `run_check` rejects a command

Only the documented allowlist is accepted. Shell operators, pipes, redirects, path-qualified executables, and unlisted subcommands are rejected. Add a proper tool or extend the allowlist deliberately; do not pass a shell string.

## File path errors

Paths are case-sensitive on Linux. Use the path exactly as it exists. The agent tries a limited case-insensitive and `-`/`_` fallback for read-only paths, but the fallback can be ambiguous and is not a substitute for the correct path.

The default read roots are:

- `~/GitHub`
- `~/Projects`
- `~/Documents`
- `~/Desktop`

The default write root is `~/GitHub`. Update the roots in the config if your projects live elsewhere.

## Security scan reports a missing target

Omit the target to scan the current workspace:

```bash
./bin/voice-agent ask "Run a quick security scan"
```

The scan is read-only, but its output can contain local system details. Review the output before sharing it.

## `playerctl` warning

`playerctl` is optional. The media tool falls back to MPRIS over D-Bus. Install `playerctl` if you want the simpler backend.

## Audit log and private data

The audit log is local at:

```text
~/.local/state/voice-agent/audit.jsonl
```

It may contain transcripts, tool arguments, and truncated tool output. Delete it if you do not want to retain that data. See [PRIVACY.md](PRIVACY.md).
