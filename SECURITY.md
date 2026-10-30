# Security Policy

## Supported versions

This is a working prototype. Security fixes are applied to the current main branch; there are no long-term support branches.

## Reporting a vulnerability

Please do not open a public issue for a security problem. Use GitHub's private vulnerability reporting for this repository if it is enabled, or contact the repository owner through GitHub. Include:

- A short description of the issue
- Steps to reproduce
- The affected command or tool
- The impact you believe is possible
- Any suggested fix

Do not include real secrets, private keys, or personal data in a report. Use redacted examples.

## Security goals

- Keep speech, prompts, and tool results local by default.
- Give the language model no arbitrary shell, no unrestricted filesystem access, and no unapproved destructive actions.
- Make dangerous operations explicit and auditable.
- Reduce the impact of a confused or manipulated model.
- Avoid collecting telemetry.

## Non-goals

- Protecting against a compromised operating system, kernel, Ollama daemon, or user account.
- Protecting against a malicious model that has already been given approval to run arbitrary project code.
- Providing a hardware-isolated virtual machine. The sandbox shares the host kernel.
- Guaranteeing that secret redaction catches every possible secret format.
- Preventing a user from deliberately enabling unsafe configuration options.

## Trust boundaries

1. **Microphone to transcript**: audio is captured locally by `parec` and transcribed locally by `whisper-cli`.
2. **Transcript to model**: the transcript is sent to the configured Ollama endpoint. The default is `http://127.0.0.1:11434`; changing `llm.url` can send prompts to another host.
3. **Model to tools**: the model may request a tool call, but only names in the local registry are accepted. Web search requires confirmation and returns untrusted data that must not be followed as instructions.
4. **Tool to host**: each tool applies its own validation. Development checks are additionally confined by bubblewrap.
5. **User approval**: writes, patches, development checks, and clipboard reads require confirmation.

## Access controls

### Tool registry

The model cannot execute arbitrary commands. It receives a fixed set of function schemas. Unknown tool names are rejected. Required arguments are validated before dispatch.

### Filesystem roots

Reads are limited to `agent.allowed_read_roots`. Writes are limited to `agent.write_roots`. Paths are resolved before checking, and sensitive directories and filename suffixes are blocked. The defaults are intentionally narrow; widen them only if you understand the impact.

### Confirmations

Confirmation is required for:

- `write_file`
- `apply_patch`
- `run_check`
- `clipboard_read`

Modes:

| Mode | Behavior |
| --- | --- |
| `gui` | Use `kdialog` or `zenity`; fall back to a terminal prompt |
| `voice` | Ask aloud and transcribe a yes/no answer; fall back to a dialog |
| `terminal` | Prompt in the current terminal |
| `auto` | Automatically choose a GUI dialog when available, otherwise a terminal prompt |

`auto` does **not** mean automatic approval. There is no built-in mode that silently approves dangerous tools.

If no confirmation channel is available, the action is denied. Denied actions stop the current request instead of being retried.

### Development-check sandbox

`run_check` only accepts an allowlist of commands and optional arguments, such as:

- `git status`, `git diff`, `git log`, `git show`, and other read-only Git subcommands; branch and remote variants are limited to listing forms
- `pytest`, `python -m pytest`, `python -m unittest`, `python -m compileall`
- `ruff check`, `ruff format --check`
- `mypy`
- `npm test`, `npm run test|lint|typecheck|build|check`
- `cargo test|check|build|clippy|fmt --check`
- `go test|vet|build`

The command runs inside bubblewrap with:

- All namespaces unshared by default
- No network unless `agent.sandbox_network` is explicitly enabled
- Read-only `/usr` and `/etc`
- A writable mount only for the workspace root
- A temporary `/tmp`
- A cleared environment with a fixed `PATH`
- Git configuration, prompts, external diff, and pagers disabled
- A timeout, process-group termination, and captured output

The sandbox is a meaningful containment layer, but it is not a virtual machine. Approved test commands can still execute project code, consume resources, or modify the mounted workspace.

### Secrets and redaction

The agent blocks common sensitive paths such as SSH keys, keyrings, browser profiles, password stores, and token files. Audit strings and tool output are passed through a best-effort redactor for common API keys, tokens, passwords, and private keys.

Tools marked safe still have privacy impact. `screenshot` writes a full-screen image to disk, `clipboard_write` replaces the clipboard, and `security_scan` can reveal local system details in its output. Treat all tool output as private.

Redaction is pattern-based. It cannot guarantee that every secret is found. Do not paste secrets into prompts, and do not point the agent at a directory containing secrets unless you accept that risk.

### Audit log

Events are appended as JSON Lines to the local state directory with mode `0600`. The log can include transcripts, tool arguments, command output, and confirmation decisions. It is not encrypted. Delete it if it contains data you do not want to retain.

## Recommendations

- Keep Ollama bound to localhost unless you intentionally expose it with authentication and transport security.
- Review web-search confirmation because the query is sent to DuckDuckGo and returned text may contain prompt-injection attempts.
- Keep `agent.sandbox_network` disabled unless a check genuinely needs the network.
- Keep write roots narrow.
- Keep `agent.services` empty unless a specific user service is needed; never add sensitive system units or broad service aliases.
- Review the confirmation preview before approving a write or command.
- Use the smallest model that works for the task; tool-call quality varies by model.
- Do not run the agent as root.
- Back up work before approving patches or test commands in an important repository.
- Treat model output as untrusted text, even when it is spoken aloud.

## Repository hygiene

Do not commit `~/.config/voice-agent/config.json`, audit logs, recordings, screenshots, downloaded models, credentials, private keys, or real transcripts. Use placeholders in documentation and inspect `git diff --cached` before publishing. The repository's ignore rules cover the normal runtime and model artifacts, but ignore rules are not a security boundary.
