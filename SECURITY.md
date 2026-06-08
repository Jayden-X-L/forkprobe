# Security Notes

forkprobe is designed as a local-first skill comparison workflow. These notes describe the security-sensitive behaviors that may be flagged by static scanners.

## Local Verdict Server

- The verdict server binds only to the loopback interface and is never exposed on external network interfaces.
- Each run creates a random verdict token. The generated report must include that token before it can write the selected winner back to the local log.
- CORS is limited to file-based reports and loopback browser origins.
- Use `--no-server` to render the report without starting the local verdict-capture server. In that mode, choices stay in the browser page.

## Remote Discovery And Skill Fetching

- `recommend.py --local-only` skips GitHub/network discovery.
- `FORKPROBE_DISCOVERY_OFFLINE=1` disables online discovery for environments that require offline operation.
- Remote skill fetching accepts HTTPS GitHub/GitLab repositories by default.
- Remote skill sources using credentials, SSH, plain HTTP, localhost, `.local` hosts, or direct IP addresses are rejected before any clone is attempted.
- Users who knowingly trust another public HTTPS host can opt in with `FORKPROBE_ALLOW_UNTRUSTED_SKILL_SOURCE=1`.

## External Commands

forkprobe may invoke local tools for explicit workflow steps:

- `git clone` is used only after a remote skill source passes validation.
- `codex exec` is used only when Codex native execution is enabled.
- Commands are passed as argument lists without `shell=True`.

## Local Data

- Task content is embedded in the generated local report so the user can compare outputs.
- Verdict logs store a task hash, candidate metadata, the selected winner, and optional handoff text.
- GitHub/network discovery uses sanitized task signals, not the raw document.
