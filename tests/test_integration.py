"""
Integration tests for forkprobe — these DO make real model calls.

The default integration path validates Codex native execution (`codex exec`) when
available. Claude Code SDK validation is opt-in because it depends on a working
Claude Code auth/session and can time out in Codex-only environments.

Run with: python3 tests/test_integration.py

These are slow (a single test takes 15-60s). Not run in CI.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_DIR / "scripts"))


@unittest.skipUnless(
    os.environ.get("FORKPROBE_RUN_CLAUDE_INTEGRATION") == "1",
    "Set FORKPROBE_RUN_CLAUDE_INTEGRATION=1 to run Claude SDK integration tests",
)
class TestRealClaudeAPI(unittest.TestCase):
    """Verify the Claude SDK path actually calls the model and returns text."""

    def test_baseline_single_run(self):
        from platform_adapter import _spawn_claude_code
        result = _spawn_claude_code(
            task_input="Reply with one word: yes or no. Are you an AI?",
            system_prompt="Answer in exactly one lowercase word.",
            skill_id="test",
            timeout=60,
        )
        self.assertIsNone(result.error, f"Unexpected error: {result.error}")
        self.assertGreater(len(result.output.strip()), 0)
        self.assertGreater(result.tokens_used, 0)
        self.assertGreater(result.latency_seconds, 0)


@unittest.skipUnless(
    os.environ.get("FORKPROBE_RUN_INTEGRATION") == "1",
    "Set FORKPROBE_RUN_INTEGRATION=1 to run integration tests",
)
class TestRealCodexNative(unittest.TestCase):
    """Verify the Codex native path can use the local Codex CLI/model config."""

    @unittest.skipUnless(
        shutil.which("codex") or Path("/Applications/Codex.app/Contents/Resources/codex").exists(),
        "Codex CLI not found",
    )
    def test_codex_native_single_run(self):
        from platform_adapter import _spawn_codex
        result = _spawn_codex(
            task_input="Reply with exactly: CODEX_NATIVE_OK",
            system_prompt="Follow the user task exactly.",
            skill_id="codex-native-test",
            timeout=90,
        )
        self.assertIsNone(result.error, f"Unexpected error: {result.error}")
        self.assertEqual(result.output.strip(), "CODEX_NATIVE_OK")
        self.assertGreater(result.tokens_used, 0)
        self.assertGreater(result.latency_seconds, 0)


@unittest.skipUnless(
    os.environ.get("FORKPROBE_RUN_INTEGRATION") == "1",
    "Set FORKPROBE_RUN_INTEGRATION=1 to run integration tests",
)
class TestFullCompareFlow(unittest.TestCase):
    """End-to-end: real CLI invocation with real skills."""

    def test_two_skill_comparison_no_server(self):
        with tempfile.TemporaryDirectory() as tmp_root:
            tmp = Path(tmp_root)
            input_file = tmp / "in.txt"
            output_file = tmp / "report.html"
            input_file.write_text("本研究旨在揭示其内在机理。", encoding="utf-8")

            t0 = time.time()
            result = subprocess.run(
                ["python3", str(PROJECT_DIR / "scripts" / "compare.py"),
                 "--input", str(input_file),
                 "--skill", "baseline",
                 "--skill", "writing-anti-ai",
                 "--output", str(output_file),
                 "--no-server"],
                capture_output=True, text=True, timeout=180, cwd=PROJECT_DIR,
            )
            elapsed = time.time() - t0

            self.assertEqual(result.returncode, 0,
                             f"compare.py exited non-zero. stderr={result.stderr[-500:]}")
            self.assertTrue(output_file.exists(), "report.html was not created")
            html = output_file.read_text()
            self.assertIn("Baseline", html)
            self.assertIn("writing-anti-ai", html)

            # Check log was written
            logs = list((PROJECT_DIR / "forkprobe-logs").glob("*.json"))
            self.assertGreaterEqual(len(logs), 1, "No log file produced")

            print(f"\n  ✓ 2-path comparison completed in {elapsed:.1f}s")


if __name__ == "__main__":
    if os.environ.get("FORKPROBE_RUN_INTEGRATION") != "1":
        print("Integration tests are skipped by default. To run them:")
        print("  FORKPROBE_RUN_INTEGRATION=1 python3 tests/test_integration.py")
        print()
        print("They make real model calls and take 30-120s.")
        print("Claude SDK integration is separate:")
        print("  FORKPROBE_RUN_CLAUDE_INTEGRATION=1 python3 tests/test_integration.py")
    unittest.main(verbosity=2)
