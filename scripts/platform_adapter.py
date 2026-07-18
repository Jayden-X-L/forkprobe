"""
Platform detection and subagent spawning abstraction.

forkprobe runs as a skill inside either Claude Code or Codex. The two platforms
expose slightly different APIs for spawning sub-tasks. Claude Code is reached
through claude-agent-sdk when available. Codex is reached through the native
`codex exec` CLI first, then the OpenAI API fallback when needed.

For v0.5 we use the official Python SDKs to call those APIs directly. This
keeps the "subagent" abstraction simple and platform-agnostic at the call site.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional


class Platform(Enum):
    CLAUDE_CODE = "claude_code"
    CODEX = "codex"
    STANDALONE = "standalone"  # for direct API calls, Phase 2+
    UNKNOWN = "unknown"


@dataclass
class SubagentResult:
    """Output from one subagent run."""
    output: str
    tokens_used: int
    latency_seconds: float
    error: Optional[str] = None


def detect_platform() -> Platform:
    """
    Detect which platform forkprobe is running inside.

    Detection strategy (in order):
    1. Env var hints set by the platform
    2. Presence of platform-specific config dirs
    3. Fall back to UNKNOWN
    """
    # Claude Code sets these env vars when running a skill
    if os.environ.get("CLAUDE_CODE_SESSION_ID") or os.environ.get("CLAUDE_PROJECT_DIR"):
        return Platform.CLAUDE_CODE

    # Codex sets these env vars when running a skill
    if (
        os.environ.get("CODEX_SESSION_ID")
        or os.environ.get("CODEX_THREAD_ID")
        or os.environ.get("CODEX_SHELL")
        or os.environ.get("OPENAI_CODEX_HOME")
    ):
        return Platform.CODEX

    # Heuristic fallback: check parent process or config dir
    home = os.path.expanduser("~")
    if os.path.isdir(os.path.join(home, ".claude")) and not os.path.isdir(os.path.join(home, ".codex")):
        return Platform.CLAUDE_CODE
    if os.path.isdir(os.path.join(home, ".codex")):
        return Platform.CODEX

    return Platform.UNKNOWN


def spawn_subagent(
    platform: Platform,
    task_input: str,
    system_prompt: str,
    skill_id: str = "baseline",
    timeout_seconds: int = 120,
) -> SubagentResult:
    """
    Spawn one subagent on the current platform.

    Args:
        platform: which platform's subagent API to use
        task_input: the user's task content
        system_prompt: the system prompt (baseline = generic; skill-augmented = skill's prompt)
        skill_id: identifier for logging
        timeout_seconds: max wait

    Returns:
        SubagentResult with output, tokens, latency
    """
    if platform == Platform.CLAUDE_CODE:
        return _spawn_claude_code(task_input, system_prompt, skill_id, timeout_seconds)
    elif platform == Platform.CODEX:
        return _spawn_codex(task_input, system_prompt, skill_id, timeout_seconds)
    elif platform == Platform.STANDALONE:
        return _spawn_standalone(task_input, system_prompt, skill_id, timeout_seconds)
    else:
        return SubagentResult(
            output="",
            tokens_used=0,
            latency_seconds=0.0,
            error=f"Unknown platform: {platform}. forkprobe v0.5 supports Claude Code and Codex only.",
        )


# --- Platform-specific implementations ---


def _spawn_claude_code(task_input: str, system_prompt: str, skill_id: str, timeout: int) -> SubagentResult:
    """
    Run a sub-task inside Claude Code using claude-agent-sdk.

    The SDK piggybacks on Claude Code's OAuth, so no API key is needed when running
    inside Claude Code. For standalone use (no Claude Code), falls back to the
    `anthropic` SDK with ANTHROPIC_API_KEY.
    """
    # Preferred path: claude-agent-sdk (uses Claude Code's existing auth)
    try:
        import asyncio
        from claude_agent_sdk import query, ClaudeAgentOptions, AssistantMessage, TextBlock, ResultMessage
    except ImportError:
        return _spawn_claude_code_via_api(task_input, system_prompt, skill_id, timeout)

    t0 = time.time()

    async def _run() -> tuple[str, int]:
        out = ""
        toks = 0
        # Some skills (e.g. paper-writer-skill) are multi-step workflows that
        # expect tool use. We disable tools (forkprobe is about comparing text
        # outputs, not running side effects), but allow enough turns for the
        # model to converge on a final answer.
        max_turns = int(os.environ.get("FORKPROBE_MAX_TURNS", "3"))
        options = ClaudeAgentOptions(
            system_prompt=system_prompt,
            max_turns=max_turns,
            allowed_tools=[],
        )
        async for msg in query(prompt=task_input, options=options):
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        out += block.text
            elif isinstance(msg, ResultMessage) and msg.usage:
                toks = (msg.usage.get("input_tokens", 0) or 0) + (msg.usage.get("output_tokens", 0) or 0)
        return out, toks

    try:
        output, tokens = asyncio.run(asyncio.wait_for(_run(), timeout=timeout))
        return SubagentResult(
            output=output,
            tokens_used=tokens,
            latency_seconds=time.time() - t0,
        )
    except asyncio.TimeoutError:
        return SubagentResult(
            output="", tokens_used=0, latency_seconds=time.time() - t0,
            error=f"Timeout after {timeout}s",
        )
    except Exception as e:
        return SubagentResult(
            output="", tokens_used=0, latency_seconds=time.time() - t0,
            error=f"{type(e).__name__}: {e}",
        )


def _spawn_claude_code_via_api(task_input: str, system_prompt: str, skill_id: str, timeout: int) -> SubagentResult:
    """
    Fallback path when claude-agent-sdk is not available: use the raw anthropic SDK
    with ANTHROPIC_API_KEY. Useful for standalone / non-Claude-Code environments.
    """
    try:
        import anthropic
    except ImportError:
        return SubagentResult(
            output="", tokens_used=0, latency_seconds=0.0,
            error="Neither claude-agent-sdk nor anthropic SDK installed. "
                  "Run: pip3 install --break-system-packages claude-agent-sdk",
        )
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return SubagentResult(
            output="", tokens_used=0, latency_seconds=0.0,
            error="ANTHROPIC_API_KEY not set and claude-agent-sdk unavailable. "
                  "Either install claude-agent-sdk (inside Claude Code) or export ANTHROPIC_API_KEY.",
        )

    model = os.environ.get("FORKPROBE_MODEL_CLAUDE", "claude-sonnet-4-5")
    max_tokens = int(os.environ.get("FORKPROBE_MAX_TOKENS", "4096"))
    client = anthropic.Anthropic(timeout=timeout)
    t0 = time.time()
    try:
        response = client.messages.create(
            model=model, max_tokens=max_tokens, system=system_prompt,
            messages=[{"role": "user", "content": task_input}],
        )
        output = "".join(getattr(b, "text", "") for b in response.content)
        tokens = response.usage.input_tokens + response.usage.output_tokens
        return SubagentResult(output=output, tokens_used=tokens, latency_seconds=time.time() - t0)
    except Exception as e:
        return SubagentResult(
            output="", tokens_used=0, latency_seconds=time.time() - t0,
            error=f"{type(e).__name__}: {e}",
        )


def _spawn_codex(task_input: str, system_prompt: str, skill_id: str, timeout: int) -> SubagentResult:
    """
    Run a Codex subagent.

    Preferred path: Codex's native non-interactive CLI, which inherits the user's
    Codex Desktop auth/config/model (for example GPT-5.5 in ~/.codex/config.toml).
    Fallback path: raw OpenAI API via OPENAI_API_KEY.
    """
    native_enabled = os.environ.get("FORKPROBE_CODEX_NATIVE", "1").lower() not in {"0", "false", "no", "off"}
    native_error = None
    if native_enabled:
        native = _spawn_codex_native(task_input, system_prompt, skill_id, timeout)
        if not native.error:
            return native
        native_error = native.error

    api = _spawn_codex_via_openai_api(task_input, system_prompt, skill_id, timeout)
    if native_error and api.error:
        api.error = f"Codex native failed: {native_error}; OpenAI API fallback failed: {api.error}"
    return api


def _codex_cli_path() -> Optional[str]:
    """Return a usable Codex CLI path if one is available."""
    candidates = [
        os.environ.get("FORKPROBE_CODEX_CLI"),
        os.environ.get("CODEX_CLI_PATH"),
        shutil.which("codex"),
        "/Applications/Codex.app/Contents/Resources/codex",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    return None


def _build_codex_native_prompt(task_input: str, system_prompt: str, skill_id: str) -> str:
    """Package forkprobe's per-skill instructions into a Codex CLI prompt."""
    return (
        "You are a forkprobe candidate runner. Produce one final answer for the original task.\n\n"
        "Rules for this isolated candidate:\n"
        "- Treat the Forkprobe skill instructions below as the governing instructions for this run.\n"
        "- Do not compare candidates, mention forkprobe, or explain this wrapper.\n"
        "- Do not modify files or ask the user follow-up questions.\n"
        "- Return only the answer to the original task.\n\n"
        f"Forkprobe candidate id: {skill_id}\n\n"
        "## Forkprobe skill instructions\n"
        f"{system_prompt}\n\n"
        "## Original task\n"
        f"{task_input}\n"
    )


def _parse_codex_tokens(text: str) -> int:
    """Best-effort parse of Codex CLI token usage from its terminal transcript."""
    match = re.search(r"tokens used\s+([0-9][0-9,]*)", text, flags=re.IGNORECASE)
    if not match:
        return 0
    return int(match.group(1).replace(",", ""))


def _tail(text: str, limit: int = 1200) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return "..." + text[-limit:]


def _spawn_codex_native(task_input: str, system_prompt: str, skill_id: str, timeout: int) -> SubagentResult:
    """
    Use `codex exec` so forkprobe inherits Codex Desktop auth and model config.

    This is the closest available script-level equivalent of "use the current
    Codex model" without requiring OPENAI_API_KEY. The CLI starts an ephemeral
    non-interactive session, writes the final answer to a temp file, and exits.
    """
    cli = _codex_cli_path()
    if not cli:
        return SubagentResult(
            output="",
            tokens_used=0,
            latency_seconds=0.0,
            error="Codex CLI not found. Set FORKPROBE_CODEX_CLI or disable native mode with FORKPROBE_CODEX_NATIVE=0.",
        )

    prompt = _build_codex_native_prompt(task_input, system_prompt, skill_id)
    sandbox = os.environ.get("FORKPROBE_CODEX_SANDBOX", "read-only")
    model = os.environ.get("FORKPROBE_MODEL_CODEX_NATIVE")
    reasoning_effort = os.environ.get("FORKPROBE_CODEX_REASONING_EFFORT")
    t0 = time.time()
    output_path = None
    transcript_handle = None
    try:
        with tempfile.NamedTemporaryFile(prefix="forkprobe-codex-", suffix=".txt", delete=False) as f:
            output_path = Path(f.name)

        cmd = [
            cli,
            "exec",
            "--ephemeral",
            "--skip-git-repo-check",
            "--sandbox",
            sandbox,
            "--output-last-message",
            str(output_path),
            "-C",
            str(Path.cwd()),
        ]
        if os.environ.get("FORKPROBE_CODEX_IGNORE_USER_CONFIG", "0").lower() in {"1", "true", "yes", "on"}:
            cmd.extend(["--ignore-user-config", "--ignore-rules"])
        if model:
            cmd.extend(["--model", model])
        if reasoning_effort:
            cmd.extend(["-c", f'model_reasoning_effort="{reasoning_effort}"'])
        cmd.append("-")

        transcript_handle = tempfile.TemporaryFile(mode="w+t", encoding="utf-8")
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=transcript_handle,
            stderr=subprocess.STDOUT,
            text=True,
        )
        if proc.stdin is not None:
            proc.stdin.write(prompt)
            proc.stdin.close()
            proc.stdin = None

        deadline = time.time() + timeout
        last_size = -1
        stable_polls = 0
        final_message_ready = False
        while time.time() < deadline:
            size = output_path.stat().st_size if output_path.exists() else 0
            if size > 0:
                if size == last_size:
                    stable_polls += 1
                else:
                    stable_polls = 0
                    last_size = size
                if stable_polls >= 4:
                    final_message_ready = True
                    break
            if proc.poll() is not None:
                break
            time.sleep(0.1)

        timed_out = proc.poll() is None and not final_message_ready
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)
        transcript_handle.flush()
        transcript_handle.seek(0)
        transcript = transcript_handle.read()
        tokens = _parse_codex_tokens(transcript)
        output = ""
        if output_path.exists():
            output = output_path.read_text(encoding="utf-8").strip()
        if not output:
            output = transcript.strip()

        if timed_out:
            return SubagentResult(
                output=output, tokens_used=tokens, latency_seconds=time.time() - t0,
                error=f"Codex CLI timeout after {timeout}s",
            )

        if proc.returncode not in {0, -15} and not final_message_ready:
            return SubagentResult(
                output=output,
                tokens_used=tokens,
                latency_seconds=time.time() - t0,
                error=f"Codex CLI exited {proc.returncode}: {_tail(transcript)}",
            )
        if not output:
            return SubagentResult(
                output="",
                tokens_used=tokens,
                latency_seconds=time.time() - t0,
                error="Codex CLI returned an empty final message.",
            )
        return SubagentResult(output=output, tokens_used=tokens, latency_seconds=time.time() - t0)
    except Exception as e:
        return SubagentResult(
            output="", tokens_used=0, latency_seconds=time.time() - t0,
            error=f"{type(e).__name__}: {e}",
        )
    finally:
        if transcript_handle is not None:
            transcript_handle.close()
        if output_path:
            try:
                output_path.unlink(missing_ok=True)
            except Exception:
                pass


def _spawn_codex_via_openai_api(task_input: str, system_prompt: str, skill_id: str, timeout: int) -> SubagentResult:
    """
    Call OpenAI Chat Completions API directly.

    Uses OPENAI_API_KEY (and OPENAI_BASE_URL if set). This is only a fallback
    for environments without Codex native CLI auth/config.
    """
    if not os.environ.get("OPENAI_API_KEY"):
        return SubagentResult(
            output="", tokens_used=0, latency_seconds=0.0,
            error="OPENAI_API_KEY not set in environment. (OpenAI API fallback requires it.)",
        )

    try:
        import openai
    except ImportError:
        return SubagentResult(
            output="", tokens_used=0, latency_seconds=0.0,
            error="openai SDK not installed. Run: pip3 install --break-system-packages openai",
        )

    model = os.environ.get("FORKPROBE_MODEL_OPENAI", "gpt-4o")
    max_tokens = int(os.environ.get("FORKPROBE_MAX_TOKENS", "4096"))

    client = openai.OpenAI(timeout=timeout)
    t0 = time.time()
    try:
        response = client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": task_input},
            ],
        )
        latency = time.time() - t0
        output = response.choices[0].message.content or ""
        tokens = (response.usage.prompt_tokens or 0) + (response.usage.completion_tokens or 0)
        return SubagentResult(output=output, tokens_used=tokens, latency_seconds=latency)
    except openai.APIStatusError as e:
        return SubagentResult(
            output="", tokens_used=0, latency_seconds=time.time() - t0,
            error=f"API {e.status_code}: {e.message}",
        )
    except openai.APIError as e:
        return SubagentResult(
            output="", tokens_used=0, latency_seconds=time.time() - t0,
            error=f"APIError: {e}",
        )
    except Exception as e:
        return SubagentResult(
            output="", tokens_used=0, latency_seconds=time.time() - t0,
            error=f"{type(e).__name__}: {e}",
        )


def _spawn_standalone(task_input: str, system_prompt: str, skill_id: str, timeout: int) -> SubagentResult:
    """
    Standalone mode: pick whichever API key is available. Useful for local CLI use
    outside Claude Code / Codex (Phase 2+).
    """
    if os.environ.get("ANTHROPIC_API_KEY"):
        return _spawn_claude_code(task_input, system_prompt, skill_id, timeout)
    if os.environ.get("OPENAI_API_KEY"):
        return _spawn_codex(task_input, system_prompt, skill_id, timeout)
    return SubagentResult(
        output="", tokens_used=0, latency_seconds=0.0,
        error="Standalone mode needs ANTHROPIC_API_KEY or OPENAI_API_KEY in env.",
    )


# --- CLI for D1 sanity-check ---

if __name__ == "__main__":
    platform = detect_platform()
    print(f"Detected platform: {platform.value}")
    print(f"  CLAUDE_CODE_SESSION_ID: {os.environ.get('CLAUDE_CODE_SESSION_ID', '(not set)')}")
    print(f"  CODEX_SESSION_ID: {os.environ.get('CODEX_SESSION_ID', '(not set)')}")
    print(f"  CODEX_THREAD_ID: {os.environ.get('CODEX_THREAD_ID', '(not set)')}")
    print(f"  CODEX_SHELL: {os.environ.get('CODEX_SHELL', '(not set)')}")
    print(f"  Codex CLI: {_codex_cli_path() or '(not found)'}")
    print(f"  ~/.claude exists: {os.path.isdir(os.path.expanduser('~/.claude'))}")
    print(f"  ~/.codex exists: {os.path.isdir(os.path.expanduser('~/.codex'))}")
