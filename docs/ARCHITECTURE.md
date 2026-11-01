# Architecture

## Pipeline

```text
┌───────────┐   PCM    ┌──────────────┐   WAV    ┌──────────────┐
│ Microphone│ ───────▶ │ Capture + VAD│ ───────▶ │ whisper.cpp  │
└───────────┘          └──────────────┘          └──────┬───────┘
                                                      │ transcript
                                                      ▼
┌──────────┐   text   ┌──────────────┐   prompt  ┌───────────────┐
│ Speakers │ ◀─────── │ Piper / TTS  │ ◀──────── │ Agent + Ollama│
└──────────┘          └──────────────┘   answer  └──────┬────────┘
                                                        │ tool call
                                                        ▼
                                      ┌──────────────────────────┐
                                      │ Tool registry + policy   │
                                      │ confirmations + sandbox  │
                                      └──────────────────────────┘
```

A wake session preloads the configured Ollama model while Jarvis speaks its greeting, then remains active until the user invokes `voice-agent stop` or speaks an exit phrase:

1. Start the selected capture backend (`parec` by default, or optional `sounddevice`).
2. Measure a short ambient calibration window.
3. Detect speech above an adaptive RMS threshold.
4. Stop after a silence window or the maximum duration.
5. Start a short acknowledgement immediately after capture completes, then transcribe with `whisper-cli`.
6. Optionally read the transcript aloud and require a bounded yes/no confirmation when enabled.
7. Send the transcript to the local Ollama model with the tool schemas.
8. Validate and execute any requested tool calls.
9. Return tool results to the model for a short spoken answer.
10. Speak the answer with Piper, falling back to `espeak-ng`.
11. Keep Ollama warm for `llm.keep_alive` (default `30m`) and unload it when stopped.

When `tts.status_updates` is enabled, the wake session speaks while listening, gives a short `Okay.` acknowledgement after capture, reports unrecognized speech and model warmup problems, and speaks a brief notice before the first safe tool runs. If `stt.confirm_transcript` is enabled, the session reads the transcript back and waits for confirmation before model processing. Unclear confirmations are retried within the configured limit; rejected requests return to listening without reaching the model. Voice confirmations for dangerous tools are controlled separately by `agent.confirmation` and remain independent of transcript confirmation. These cues respect `tts.enabled` and can be disabled independently from spoken answers.

The microphone is inactive before `wake` starts. A wake session retains a bounded conversation history between turns; both message count and character budget limit retained history, so stale large tool results do not crowd out the current request. History is in memory only and is discarded when the process exits. The stop command can interrupt capture and speech playback.

Optional `audio.barge_in` starts a second capture path during speech playback. It is disabled by default because this implementation has no acoustic echo cancellation; enable it only when the microphone does not hear the speakers strongly.

## Components

| Component | Responsibility |
| --- | --- |
| `bin/voice-agent` | Sets `PYTHONPATH` and starts the Python module |
| `src/voice_agent/cli.py` | Command-line interface and `doctor` checks |
| `src/voice_agent/agent.py` | System prompt, model loop, confirmations, tool dispatch |
| `src/voice_agent/llm.py` | Ollama HTTP client, model discovery, `keep_alive` |
| `src/voice_agent/audio.py` | Capture, calibration, VAD, WAV writing |
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

The default `parec` capture path uses an adaptive threshold:

- Calibrate on the first `audio.calibration_ms` milliseconds.
- Use a low percentile of the calibration RMS as the noise floor.
- Apply `audio.noise_multiplier` with a minimum and maximum clamp.
- Require `audio.speech_chunks` consecutive chunks above the threshold.
- Stop after `audio.silence_ms` below the threshold or `audio.max_seconds` of speech.
- Stop waiting for speech after `audio.no_speech_timeout_ms` following calibration.

Endpoint decisions terminate the capture process immediately; the outer watchdog is only a final safety fallback.

If the default capture path is noisy, increase `audio.noise_multiplier` or set a specific `audio.source`. If speech is missed, lower `audio.min_rms`. The optional `sounddevice` path uses WebRTC VAD instead of the adaptive RMS threshold.

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
- All Linux capabilities dropped
- Read-only system mounts, including `/usr/local` when present
- A read-write bind of the workspace root only
- A temporary home and `/tmp`
- A cleared environment
- Output and time limits

The sandbox is intentionally conservative, but it is not a VM boundary. See [../SECURITY.md](../SECURITY.md).

## Configuration and state

Configuration is merged over defaults and stored as JSON with mode `0600`. Runtime state, reminders, and email drafts live in the XDG state directory. Models, binaries, and the optional browser profile live in the XDG data directory. See [CONFIGURATION.md](CONFIGURATION.md) and [PRIVACY.md](PRIVACY.md).

## Design constraints

- Standard-library Python only; no runtime package installation
- Explicitly started microphone session instead of an always-on audio daemon
- No telemetry or cloud dependencies
- Fixed tools instead of shell access
- Capability-scoped plugins for broad user-level services and applications
- Optional systemd user supervision for restartable sessions and the local reminder watcher
- Optional sounddevice/WebRTC-VAD streaming capture with PipeWire echo-cancel source support
- Conservative defaults, with explicit opt-in for broader access
- Low idle cost: no process remains after `stop`, and the model unloads explicitly
