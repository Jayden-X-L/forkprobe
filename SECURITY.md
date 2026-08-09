# Security Notes

forkprobe is designed as a local-first skill comparison workflow. These notes describe the security-sensitive behaviors that may be flagged by static scanners.

## Local Verdict Server

- The verdict server binds only to the loopback interface and is never exposed on external network interfaces.
- Each run creates a random verdict token. The generated report must include that token before it can write the selected winner back to the local log.
- CORS is limited to file-based reports and loopback browser origins.
- Use `--no-server` to render the report without starting the local verdict-capture server. In that mode, choices stay in the browser page.

## Optional Anonymous Selection Sharing

- The Report sends verdicts only to the tokenized loopback server. It never sends telemetry directly from browser JavaScript.
- Anonymous sharing is controlled inline beside the Continue action. First use defaults to checked; the user's explicit Continue preference is stored in `~/.forkprobe/config.json`.
- The outbound event contains only a privacy-safe task type, compared Skill names, and final choice, plus a random event ID and schema version for idempotency.
- Raw task text, candidate output, generated files, reasons, local paths, and identity fields are never included. The Worker rejects unexpected fields.
- Opted-in events are queued in `~/.forkprobe/telemetry/outbox/` before transmission. Network or receiver failures do not block local verdict persistence or Agent continuation.
- Set `FORKPROBE_TELEMETRY=0` to force sharing off. Configure a self-hosted or project receiver with `FORKPROBE_TELEMETRY_ENDPOINT`.
- The reference Cloudflare Worker stores raw anonymous events in D1 and exposes aggregate statistics only after the minimum sample threshold. It does not store request IP addresses in D1.

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

## Web Artifact Preview

- `web_artifact.py` serves each generated site from a temporary loopback-only HTTP server for screenshots; it does not bind to external interfaces.
- Chrome/Chromium runs headlessly with a fresh temporary profile for each screenshot.
- When the Python Playwright package is available, it launches the same local Chrome/Chromium executable against the loopback preview to measure rendered mobile overflow; no remote browser service is used.
- Generated webpage files are not deployed. The report links to local files, and generic embedded HTML previews use a sandboxed iframe.
- Generated website code is untrusted output. Inspect `qa.json` and the source package before deploying it or connecting it to credentials, production APIs, or private data.

## Local Data

- Task content is embedded in the generated local report so the user can compare outputs.
- Verdict logs store a task hash, candidate metadata, the selected winner, optional local reason, and handoff text.
- GitHub/network discovery uses sanitized task signals, not the raw document.
