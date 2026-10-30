# Contributing

Thanks for helping improve Aegis. This project is a local-first voice assistant with a deliberately small attack surface. Changes that broaden filesystem, command, or network access need extra review.

## Development setup

```bash
git clone <repo-url> voice-agent
cd voice-agent
./scripts/setup.sh
ollama pull qwen3:1.7b
./bin/voice-agent doctor
```

The setup script installs local binaries and models under the XDG data directory. It does not install Python packages; the runtime uses the standard library.

## Checks before opening a pull request

```bash
python3 -m compileall -q src
./bin/voice-agent doctor
./bin/voice-agent tools
```

If you change audio, STT, TTS, or the model loop, test with a short spoken command and a text command:

```bash
./bin/voice-agent ask "What is the current volume?"
./bin/voice-agent wake
```

If you change the sandbox or file tools, add a focused test or document a reproducible manual check. Do not test with real secrets or private data.

## Code style

- Keep the runtime dependency-free unless a new dependency is clearly justified.
- Prefer small, explicit functions and argv arrays over shell strings.
- Do not add comments that merely restate the code.
- Keep the system prompt compact; it affects cold-start latency.
- Keep tool descriptions short and accurate.
- Preserve the existing confirmation and allowlist behavior unless the change is a deliberate security review.

## Security-sensitive changes

Treat these as security changes:

- Adding or changing a tool
- Changing file roots, sensitive-path checks, or redaction
- Changing confirmation behavior
- Changing bubblewrap arguments or the command allowlist
- Enabling network access in the sandbox
- Adding a remote endpoint or telemetry
- Changing how transcripts, tool output, or audit records are stored

For these changes, explain the threat model, the new trust boundary, and how failure is handled. A tool should fail closed if validation or confirmation is unavailable.

## Privacy

Do not commit:

- Personal configuration
- Audit logs
- Backups
- Audio recordings
- Downloaded models or binaries
- Screenshots
- API keys, tokens, passwords, or private keys
- Absolute home-directory paths from your machine
- Hostnames, usernames, MAC addresses, or other machine-specific identifiers

Use placeholders such as `~/Projects/example` in examples.

## Commit and pull request scope

Keep pull requests focused. Include:

- What changed
- Why it changed
- How it was tested
- Any security or privacy impact
- Any migration or configuration changes

## Licensing

No project license has been selected yet. Do not assume a license for contributions until the repository owner adds one. Discuss licensing before contributing substantial code.
