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
| `volume` | Safe | Reads or changes the default output volume |
| `media` | Safe | Controls the active media player through `playerctl` or MPRIS |
| `lock_screen` | Safe | Locks the current desktop session |
| `screenshot` | Safe | Captures the screen and saves a PNG under `Pictures/Screenshots` |
| `notify` | Safe | Shows a short desktop notification |
| `system_info` | Safe | Reads memory, disk, uptime, GPU, CPU, kernel, or a summary |
| `battery` | Safe | Reads battery charge and charging status |
| `clipboard_read` | Confirmation | Reads the current clipboard into the request context |
| `clipboard_write` | Safe | Copies text to the clipboard |
| `list_files` | Safe | Lists a directory inside the read roots |
| `read_file` | Safe | Reads a text file inside the read roots; binary files are refused |
| `search_files` | Safe | Searches text files with a regular expression |
| `write_file` | Confirmation | Creates or replaces a file inside the write roots |
| `apply_patch` | Confirmation | Replaces one exact text occurrence inside the write roots |
| `run_check` | Confirmation | Runs an allowlisted development check in a bubblewrap sandbox |
| `security_scan` | Safe | Performs a read-only local security review |

## Notes per tool

### `open_app`

Only the spoken names in `agent.apps` are accepted. Each entry is an argv array, not a shell command. Add or remove apps in the configuration.

### `volume` and `media`

Volume uses `wpctl` (PipeWire/WirePlumber). Media uses `playerctl` when available and falls back to MPRIS over D-Bus. Media control is limited to play, pause, next, previous, stop, and status.

### `screenshot`

Saves an image file. It does not read the image back into the model. Treat the saved file as potentially sensitive because it can contain anything visible on screen.

### `system_info` and `battery`

These read local status only. They do not change system state. The GPU section is optional and depends on the available vendor tools.

### `clipboard_read` and `clipboard_write`

`clipboard_read` requires confirmation because clipboards commonly contain passwords, tokens, or private text. `clipboard_write` changes the clipboard but does not read it.

### File tools

File reads are limited to `agent.allowed_read_roots`; writes are limited to `agent.write_roots`. Paths are resolved before the root check. Common sensitive directories and filename suffixes are blocked. Reads redact common secret patterns and refuse files that look binary.

`write_file` and `apply_patch` create a backup in the state directory before changing an existing file. `apply_patch` requires `old_text` to occur exactly once.

`search_files` skips common vendor, cache, and version-control directories, has a result limit, and does not open sensitive paths.

### `run_check`

`run_check` is the only tool that executes project commands. It is restricted to:

- Read-only Git subcommands such as `status`, `diff`, `log`, `show`, `rev-parse`, `ls-files`, `grep`, `blame`, `describe`, and `shortlog`; `branch` and `remote` are restricted to listing and URL-read forms
- `pytest`, `python -m pytest`, `python -m unittest`, and `python -m compileall`
- `ruff check` and `ruff format --check`
- `mypy`
- `npm test` and `npm run test|lint|typecheck|build|check`
- `cargo test|check|build|clippy|fmt --check`
- `go test|vet|build`

Shell metacharacters, path-qualified executables, and unlisted subcommands are rejected. The command runs inside bubblewrap with no network by default, a read-only system, a writable workspace mount, a temporary home, and a timeout.

Approved test commands can still run project code. Review the confirmation preview and only run checks in workspaces you trust.

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
