# Tools

The model can only use tools in this fixed registry. It cannot run arbitrary shell commands. List the registry from the current installation with:

```bash
./bin/voice-agent tools
```

A tool marked **confirmation** requires explicit approval before it runs. Read [SECURITY.md](../SECURITY.md) before widening file roots or enabling network access in the sandbox.

## Tool reference

| Tool | Risk | What it does |
| --- | --- | --- |
| `open_app` | Safe | Opens an app from the configured allowlist by name |
| `open_url` | Safe | Opens any HTTP(S) website in the configured visible desktop browser |
| `service` | Confirmation | Controls only configured user-systemd services |
| `power` | Confirmation | Requests only configured suspend, hibernate, poweroff, or reboot actions |
| `volume` | Safe | Reads or changes the default output volume |
| `media` | Safe | Controls the active media player through `playerctl` or MPRIS and reads track metadata |
| `lock_screen` | Safe | Locks the current desktop session |
| `screenshot` | Safe | Captures the screen and saves a PNG under `Pictures/Screenshots` |
| `notify` | Safe | Shows a short desktop notification |
| `system_info` | Safe | Reads memory, disk, uptime, GPU, CPU, kernel, or a summary |
| `battery` | Safe | Reads battery charge and charging status |
| `web_search` | Confirmation | Searches DuckDuckGo HTML for bounded reference results; returned web text is untrusted |
| `reminder` | Confirmation | Stores local reminders; due items notify during wake or through `reminders watch` |
| `email_draft` | Confirmation | Creates, lists, shows, or deletes local `.eml` drafts; never sends email |
| `clipboard_read` | Confirmation | Reads the current clipboard into the request context |
| `clipboard_write` | Safe | Copies text to the clipboard |
| `list_files` | Safe | Lists a directory inside the read roots |
| `read_file` | Safe | Reads a text file inside the read roots; binary files are refused |
| `search_files` | Safe | Searches text files with a regular expression |
| `write_file` | Confirmation | Creates or replaces a file inside the write roots |
| `apply_patch` | Confirmation | Replaces one exact text occurrence inside the write roots |
| `run_check` | Confirmation | Runs an allowlisted development check in a bubblewrap sandbox |
| `security_scan` | Safe | Performs a read-only local security review |
| `browser_open` | Confirmation | Opens an allowlisted HTTPS page and returns bounded untrusted text (optional) |
| `browser_interact` | Confirmation | Clicks, types, presses, selects, hovers, or waits on one allowlisted page element (optional) |

## Notes per tool

### `open_app`

Only the spoken names in `agent.apps` are accepted. Each entry is an argv array, not a shell command. Add or remove apps in the configuration.

### `service` and `power`

`service` accepts only names in `agent.services` and always uses `systemctl --user`; it cannot control system-wide units. `power` accepts only actions in `agent.power_actions`. Both require runtime confirmation, and an unavailable or unconfigured action is rejected.

The `stop` command also asks Ollama to unload the configured model with `keep_alive: 0`; this is best-effort if Ollama is unavailable.

Wake sessions record local phase timings for capture, transcription, agent/tool processing, and speech in the audit log. These timings contain no transcript content and help identify latency bottlenecks.

Starting `wake` also performs a one-token Ollama warm-up with the configured model. This loads the model before the first user request without sending user content.

### `volume` and `media`

Volume uses `wpctl` (PipeWire/WirePlumber). Media uses `playerctl` when available and falls back to MPRIS over D-Bus. Media control is limited to play, pause, next, previous, stop, status, and track metadata. Metadata requires `playerctl`; MPRIS fallback still supports transport controls and status.

For a request such as “play a song”, the agent calls `media` with `action: "play"`. If no MPRIS player is active, it starts the configured `agent.media_player` (Elisa by default). To play a local track, pass its name or path in `track`; matching files are limited to `agent.music_roots`. Use `action: "metadata"` to ask what is currently playing.

### `open_url`

`open_url` launches the configured `agent.apps.browser` command with an HTTP or HTTPS URL. It is the normal path for “open this website” and opens a visible desktop browser. `browser_open` remains the optional restricted automation path for inspecting or interacting with an allowlisted page.

### `screenshot`

Saves an image file. It does not read the image back into the model. Treat the saved file as potentially sensitive because it can contain anything visible on screen.

### `system_info` and `battery`

These read local status only. They do not change system state. The GPU section is optional and depends on the available vendor tools.

### `web_search`

Search is restricted to DuckDuckGo's HTTPS HTML endpoint. It accepts a bounded text query and returns only a small number of titles, snippets, and links. Results are untrusted reference data and are never treated as instructions. Search requires confirmation because it sends the query outside the machine.

### `clipboard_read` and `clipboard_write`

`clipboard_read` requires confirmation because clipboards commonly contain passwords, tokens, or private text. `clipboard_write` changes the clipboard but does not read it.

### File tools

File reads are limited to `agent.allowed_read_roots`; writes are limited to `agent.write_roots`. Paths are resolved before the root check. Common sensitive directories and filename suffixes are blocked, including cloud and container credentials, signing keys, and token stores. Reads are capped at 5 MB, redact common secret patterns, and refuse files that look binary.

`write_file` and `apply_patch` create a backup in the state directory before changing an existing file. `apply_patch` requires `old_text` to occur exactly once.

`search_files` skips common vendor, cache, version-control, and sensitive directories, has a result limit, and does not open sensitive paths.

### `run_check`

`run_check` is the only tool that executes project commands. It is restricted to:

- Read-only Git subcommands such as `status`, `diff`, `log`, `show`, `rev-parse`, `ls-files`, `grep`, `blame`, `describe`, and `shortlog`; `branch` and `remote` are restricted to listing and URL-read forms
- `pytest`, `python -m pytest`, `python -m unittest`, and `python -m compileall`
- `ruff check` and `ruff format --check`; `--fix` and `--unsafe-fixes` are rejected
- `mypy`
- `npm test` and `npm run test|lint|typecheck|build|check`
- `cargo test|check|build|clippy|fmt --check`; `cargo clippy --fix` is rejected
- `go test|vet|build`

Shell metacharacters, path-qualified executables, and unlisted subcommands are rejected. The command runs inside bubblewrap with no network by default, a read-only system, a writable workspace mount, a temporary home, and a timeout.

Approved test commands can still run project code. Review the confirmation preview and only run checks in workspaces you trust.

### `reminder` and `email_draft`

Reminders are stored in `~/.local/state/voice-agent/reminders.json` behind a file lock so wake sessions and the CLI watcher cannot corrupt each other. `voice-agent reminders watch` delivers due reminders in the background; `voice-agent reminders list`, `create`, and `cancel` manage them from the terminal. The optional `systemd/voice-agent-reminders.service` runs the watcher as a user service.

Email drafts are mode-`0600` `.eml` files under `~/.local/state/voice-agent/email-drafts/`. Supported actions are `create`, `list`, `show`, and `delete`. The tool never sends mail, contacts a provider, or reads mailbox credentials.

### `browser_open` and `browser_interact`

These tools are registered only when `agent.browser.enabled` is true. They use an isolated Playwright profile, allow only HTTPS URLs on `agent.browser.allowed_domains`, abort subrequests outside that allowlist, block service workers, and require confirmation. Page text is returned as bounded untrusted data.

`browser_interact` supports one action at a time: `click`, `type`, `press`, `select`, `hover`, or `wait`. It does not upload files, download files, run scripts, or attach to the user's normal browser profile. Treat every page as potentially hostile and keep the domain allowlist narrow.

### `security_scan`

The scan is read-only and covers:

- Secret-like strings in the target tree
- Risky file modes
- Listening sockets
- Firewall and SELinux status
- Failed system and user services
- SSH daemon settings when readable
- SUID/SGID count
- Recent system errors

It does not open known sensitive files. The scan can still reveal local system details in its output, so treat the result as private.

## Adding a tool

Keep the security model intact:

1. Define a narrow schema with required fields.
2. Validate every path against the configured roots.
3. Avoid shell execution; use argv arrays.
4. Mark privacy-sensitive or mutating tools as `dangerous=True`.
5. Add an audit record for meaningful state changes.
6. Add a test or a reproducible manual check.
