# Architecture

## Pipeline

```text
┌──────────┐   PCM    ┌──────────────┐   WAV    ┌──────────────┐
│ Microphone│ ───────▶ │ parec + VAD  │ ───────▶ │ whisper.cpp  │
└──────────┘          └──────────────┘          └──────┬───────┘
                                                      │ transcript
                                                      ▼
┌──────────┐   text   ┌──────────────┐   prompt  ┌──────────────┐
│ Speakers │ ◀─────── │ Piper / TTS  │ ◀──────── │ Agent + Ollama│
└──────────┘          └──────────────┘   answer  └──────┬───────┘
                                                      │ tool call
                                                      ▼
                                       ┌──────────────────────────┐
                                       │ Tool registry + policy   │
                                       │ confirmations + sandbox  │
                                       └──────────────────────────┘
```

A wake session remains active until the user invokes `voice-agent stop` or speaks an exit phrase:

1. Start `parec`.
2. Measure a short ambient calibration window.
3. Detect speech above an adaptive RMS threshold.
4. Stop after a silence window or the maximum duration.
5. Transcribe with `whisper-cli`.
6. Send the transcript to the local Ollama model with the tool schemas.
7. Validate and execute any requested tool calls.
8. Return tool results to the model for a short spoken answer.
9. Speak the answer with Piper, falling back to `espeak-ng`.
10. Let Ollama unload the model after `llm.keep_alive` (default `10s`).

The microphone is inactive before `wake` starts. The session has no persistent conversation context between turns, and the stop command can interrupt capture and speech playback.

## Components

| Component | Responsibility |
| --- | --- |
| `bin/voice-agent` | Sets `PYTHONPATH` and starts the Python module |
| `src/voice_agent/cli.py` | Command-line interface and `doctor` checks |
| `src/voice_agent/agent.py` | System prompt, model loop, confirmations, tool dispatch |
| `src/voice_agent/llm.py` | Ollama HTTP client, model discovery, `keep_alive` |
| `src/voice_agent/audio.py` | Capture, calibration, VAD, WAV writing, beeps |
| `src/voice_agent/stt.py` | whisper.cpp wrapper |
| `src/voice_agent/tts.py` | Piper and `espeak-ng` wrappers, speech-safe text cleanup |
| `src/voice_agent/sandbox.py` | Bubblewrap command construction and execution |
| `src/voice_agent/tools/` | Fixed tool definitions and handlers |
| `src/voice_agent/audit.py` | JSONL audit log |
| `src/voice_agent/config.py` | XDG paths, defaults, deep merge, atomic save |

## Model and tool loop

The model receives:

- A compact system prompt with safety rules, the current time, the workspace, and the allowed roots
- The user transcript
- Function schemas for the fixed tool registry

The loop supports up to `llm.max_steps` model turns (default 6). A turn may contain native tool calls or a JSON tool call in the content. Some local models wrap JSON calls in Markdown fences, so the agent also accepts fenced JSON.

When a tool returns a result, the result is sent back as a tool message. If the model repeats the same tool call with the same arguments, the agent stops executing duplicates and asks the model for a final answer without tools. If the loop reaches its step limit, the agent also performs a final no-tool synthesis pass. This keeps small models from looping indefinitely.

Denied confirmations terminate the request. The model is not allowed to retry a denied action.

## Audio endpointing

The capture code uses an adaptive threshold:

- Calibrate on the first `audio.calibration_ms` milliseconds.
- Use a low percentile of the calibration RMS as the noise floor.
- Apply `audio.noise_multiplier` with a minimum and maximum clamp.
- Require `audio.speech_chunks` consecutive chunks above the threshold.
- Stop after `audio.silence_ms` below the threshold or `audio.max_seconds` of speech.

If the microphone is noisy, increase `audio.noise_multiplier` or set a specific `audio.source`. If speech is missed, lower `audio.min_rms`.

## Confirmation design

The confirmation decision happens in the runtime, not in the model:

- The runtime builds a human-readable preview.
- The user approves or denies through the configured channel.
- The model only sees the result.
- A denial produces a `DENIED` tool result and stops the loop.

This prevents a prompt from talking the model into bypassing confirmation.

## Sandbox design

`run_check` parses a command string with `shlex`, rejects shell metacharacters, and validates the executable and subcommand against an allowlist. It then runs the command through `bwrap` with:

- `--unshare-all` (including network by default)
- `--die-with-parent`
- Read-only system mounts
- A read-write bind of the workspace root only
- A temporary home and `/tmp`
- A cleared environment
- Output and time limits

The sandbox is intentionally conservative, but it is not a VM boundary. See [../SECURITY.md](../SECURITY.md).

## Configuration and state

Configuration is merged over defaults and stored as JSON with mode `0600`. Runtime state lives in the XDG state directory. Models and binaries live in the XDG data directory. See [CONFIGURATION.md](CONFIGURATION.md) and [PRIVACY.md](PRIVACY.md).

## Design constraints

- Standard-library Python only; no runtime package installation
- Explicitly started continuous session instead of an always-on background daemon
- No telemetry or cloud dependencies
- Fixed tools instead of shell access
- Conservative defaults, with explicit opt-in for broader access
- Low idle cost: no process remains after a command, and the model unloads quickly
