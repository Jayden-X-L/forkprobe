"""
Smoke tests for forkprobe. Run with: python3 tests/test_smoke.py

These cover the non-network logic: imports, catalog loading, skill parsing,
report rendering, log writing. They do NOT call the live LLM API (that's the
integration test job — see tests/test_integration.py).
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch

# Ensure scripts/ is importable
PROJECT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_DIR / "scripts"))


class TestPlatformAdapter(unittest.TestCase):
    def test_imports(self):
        from platform_adapter import detect_platform, spawn_subagent, Platform, SubagentResult
        self.assertTrue(callable(detect_platform))
        self.assertTrue(callable(spawn_subagent))
        # Enum has all expected members
        self.assertEqual({p.value for p in Platform}, {"claude_code", "codex", "standalone", "unknown"})

    def test_detect_platform_returns_enum(self):
        from platform_adapter import detect_platform, Platform
        result = detect_platform()
        self.assertIsInstance(result, Platform)

    def test_codex_without_native_or_key_returns_clear_error(self):
        from platform_adapter import _spawn_codex
        # Ensure no native Codex CLI path and no OpenAI key.
        old_native = os.environ.get("FORKPROBE_CODEX_NATIVE")
        old = os.environ.pop("OPENAI_API_KEY", None)
        os.environ["FORKPROBE_CODEX_NATIVE"] = "0"
        try:
            r = _spawn_codex("hi", "system", "test", 30)
            self.assertIsNotNone(r.error)
            self.assertIn("OPENAI_API_KEY", r.error)
        finally:
            if old_native is None:
                os.environ.pop("FORKPROBE_CODEX_NATIVE", None)
            else:
                os.environ["FORKPROBE_CODEX_NATIVE"] = old_native
            if old:
                os.environ["OPENAI_API_KEY"] = old

    def test_codex_prefers_native_cli(self):
        from platform_adapter import _spawn_codex
        with tempfile.TemporaryDirectory() as tmp:
            fake_cli = Path(tmp) / "codex"
            prompt_path = Path(tmp) / "prompt.txt"
            fake_cli.write_text(
                "#!/usr/bin/env python3\n"
                "import os, pathlib, sys\n"
                "args = sys.argv[1:]\n"
                "out = pathlib.Path(args[args.index('--output-last-message') + 1])\n"
                "prompt = sys.stdin.read()\n"
                "pathlib.Path(os.environ['FORKPROBE_FAKE_PROMPT_PATH']).write_text(prompt, encoding='utf-8')\n"
                "out.write_text('native answer', encoding='utf-8')\n"
                "print('native answer')\n"
                "print('tokens used\\n1,234', file=sys.stderr)\n",
                encoding="utf-8",
            )
            os.chmod(fake_cli, 0o755)

            keys = ("FORKPROBE_CODEX_CLI", "FORKPROBE_FAKE_PROMPT_PATH", "FORKPROBE_CODEX_NATIVE", "OPENAI_API_KEY")
            old_env = {k: os.environ.get(k) for k in keys}
            os.environ["FORKPROBE_CODEX_CLI"] = str(fake_cli)
            os.environ["FORKPROBE_FAKE_PROMPT_PATH"] = str(prompt_path)
            os.environ["FORKPROBE_CODEX_NATIVE"] = "1"
            os.environ.pop("OPENAI_API_KEY", None)
            try:
                r = _spawn_codex("original task", "skill instructions", "skill-x", 30)
                self.assertIsNone(r.error)
                self.assertEqual(r.output, "native answer")
                self.assertEqual(r.tokens_used, 1234)
                prompt = prompt_path.read_text(encoding="utf-8")
                self.assertIn("skill instructions", prompt)
                self.assertIn("original task", prompt)
                self.assertIn("skill-x", prompt)
            finally:
                for k, v in old_env.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v


class TestSkillLoader(unittest.TestCase):
    def test_imports(self):
        from skill_loader import load_skill, LoadedSkill, _parse_yaml_frontmatter

    def test_parse_simple_frontmatter(self):
        from skill_loader import _parse_yaml_frontmatter
        text = (
            "---\n"
            "name: foo\n"
            "description: A test skill\n"
            "---\n"
            "Body content here.\n"
        )
        fm, body = _parse_yaml_frontmatter(text)
        self.assertEqual(fm["name"], "foo")
        self.assertEqual(fm["description"], "A test skill")
        self.assertEqual(body.strip(), "Body content here.")

    def test_parse_no_frontmatter(self):
        from skill_loader import _parse_yaml_frontmatter
        fm, body = _parse_yaml_frontmatter("Just markdown.")
        self.assertEqual(fm, {})
        self.assertEqual(body, "Just markdown.")

    def test_load_local_skill(self):
        """Create a tiny SKILL.md on disk and load it."""
        from skill_loader import load_skill
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = Path(tmp) / "my-skill"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text(
                "---\n"
                "name: my-skill\n"
                "description: Test skill for smoke test\n"
                "---\n"
                "Be helpful and polite.\n"
            )
            skill = load_skill(skill_id="ls", source=str(skill_dir))
            self.assertEqual(skill.name, "my-skill")
            self.assertEqual(skill.description, "Test skill for smoke test")
            self.assertIn("Be helpful", skill.body)
            # System prompt should embed both description and body
            prompt = skill.to_system_prompt()
            self.assertIn("Test skill for smoke test", prompt)
            self.assertIn("Be helpful", prompt)


class TestCatalog(unittest.TestCase):
    def test_catalog_loads(self):
        from compare import load_catalog
        catalog = load_catalog("academic-writing")
        self.assertEqual(catalog["domain"], "academic-writing")
        self.assertGreaterEqual(len(catalog["skills"]), 4)
        # Each skill has required fields
        for s in catalog["skills"]:
            for field in ("id", "name", "author", "language", "category", "source", "license"):
                self.assertIn(field, s, f"Missing {field} in skill {s.get('id')}")

    def test_diversity_matrix_complete(self):
        """The 2x2 anti-AI/academic × zh/en matrix should have at least 1 skill per cell."""
        from compare import load_catalog
        catalog = load_catalog("academic-writing")
        matrix = catalog["diversity_matrix"]
        for cell_name, skills_in_cell in matrix.items():
            self.assertGreaterEqual(len(skills_in_cell), 1, f"Cell {cell_name} is empty")

    def test_resolve_baseline(self):
        from compare import load_catalog, resolve_skill
        catalog = load_catalog()
        spec = resolve_skill("baseline", catalog)
        self.assertEqual(spec.id, "baseline")
        self.assertIn("helpful", spec.system_prompt.lower())

    def test_resolve_invalid_raises(self):
        from compare import load_catalog, resolve_skill
        catalog = load_catalog()
        with self.assertRaises(KeyError):
            resolve_skill("does-not-exist-123", catalog)

    def test_resolve_byo_subdir_fragment(self):
        from compare import load_catalog, resolve_skill, split_byo_source
        self.assertEqual(
            split_byo_source("https://example.com/repo#skills/demo"),
            ("https://example.com/repo", "skills/demo"),
        )
        catalog = load_catalog()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill_dir = root / "skills" / "demo"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                "---\n"
                "name: demo-subdir-skill\n"
                "description: Demo subdir skill\n"
                "---\n"
                "Use the demo instructions.\n",
                encoding="utf-8",
            )
            spec = resolve_skill(f"{root}#skills/demo", catalog)
            self.assertEqual(spec.id, "byo:demo-subdir-skill")
            self.assertIn("Use the demo instructions", spec.system_prompt)


class TestRecommendations(unittest.TestCase):
    def test_recommend_chinese_academic_writing(self):
        from recommend import recommend_candidates
        rec = recommend_candidates("我想比较几个科研写作 skill，帮我润色中文 SCI 论文段落。", online_discovery=False)
        ids = [c.id for c in rec.candidates]
        self.assertIn("baseline", ids)
        self.assertIn("writing-anti-ai", ids)
        self.assertIn("research-paper-writing-skills", ids)

    def test_recommend_nature_polishing_byo(self):
        from recommend import recommend_candidates
        rec = recommend_candidates("请比较几个 skill 做英文摘要润色，偏 Nature 风格，中译英。", online_discovery=False)
        command_args = [c.command_arg for c in rec.candidates]
        self.assertIn("https://github.com/Yuan1z0825/nature-skills#skills/nature-polishing", command_args)
        self.assertIn("--judge", rec.suggested_command)

    def test_recommend_figure_notes_text_first_scope(self):
        from recommend import recommend_candidates
        rec = recommend_candidates("我想比较几个 skill 来画科研示意图和 figure storyline。", online_discovery=False)
        command_args = [c.command_arg for c in rec.candidates]
        self.assertIn("https://github.com/Yuan1z0825/nature-skills#skills/nature-figure", command_args)
        self.assertTrue(rec.notes_zh)

    def test_recommend_ppt_request_routes_to_artifact_mode(self):
        from recommend import recommend_candidates
        rec = recommend_candidates("基于一个文档，我想做一个PPT，但是我想多对比几个skill的效果。", online_discovery=False)
        self.assertEqual(rec.deliverable_type, "pptx")
        self.assertEqual(rec.compare_mode, "artifact")
        ids = [c.id for c in rec.candidates]
        self.assertIn("baseline+presentations", ids)
        self.assertIn("academic-pptx-skill+presentations", ids)
        self.assertIn("ppt-master", ids)
        self.assertIn("md-slides", ids)
        self.assertEqual(rec.suggested_command, [])

    def test_discover_pptx_shortlist_includes_external_skills(self):
        from discover_skills import discover
        report = discover(deliverable="pptx", query="academic PPT from document", limit=5)
        ids = [candidate.id for candidate in report.shortlist]
        self.assertIn("academic-pptx-skill+presentations", ids)
        self.assertIn("nature-paper2ppt+presentations", ids)
        self.assertIn("ppt-master", ids)
        self.assertIn("md-slides", ids)
        academic = next(candidate for candidate in report.candidates if candidate.id == "academic-pptx-skill")
        self.assertEqual(academic.role, "strategy")
        self.assertTrue(academic.needs_generator)

    def test_recommend_ppt_outline_stays_text_mode(self):
        from recommend import recommend_candidates
        rec = recommend_candidates("请比较几个 skill 做 PPT 方案，不要生成 PPTX 文件，只给推荐页数和每页标题。", online_discovery=False)
        self.assertEqual(rec.deliverable_type, "ppt_outline")
        self.assertEqual(rec.compare_mode, "text")
        command_args = [c.command_arg for c in rec.candidates]
        self.assertIn("https://github.com/Yuan1z0825/nature-skills#skills/nature-paper2ppt", command_args)
        self.assertIn("--judge", rec.suggested_command)

    def test_recommend_merges_local_and_online_discovery(self):
        from discover_skills import OnlineDiscoveryReport, OnlineSkillCandidate
        from recommend import recommend_candidates

        fake_report = OnlineDiscoveryReport(
            deliverable="text",
            queries=["claude skill anti ai writing humanize"],
            candidates=[
                OnlineSkillCandidate(
                    id="github:example-lab-writing-skill",
                    name="lab-writing-skill",
                    source="https://github.com/example/lab-writing-skill",
                    command_arg="https://github.com/example/lab-writing-skill#skills/lab-writing",
                    summary_zh="GitHub 发现候选，适合内部分享稿润色。",
                    summary_en="GitHub-discovered writing candidate.",
                    score=96,
                    stars=321,
                    category="github_discovered",
                    skill_path="skills/lab-writing/SKILL.md",
                )
            ],
            notes_zh=["已用脱敏任务信号做 GitHub/网络 discovery。"],
            notes_en=["Used sanitized discovery queries."],
        )
        with patch("recommend.discover_online_skills", return_value=fake_report):
            rec = recommend_candidates("我想比较几个 skill，把内部分享稿润色得更自然。", online_discovery=True)

        ids = [candidate.id for candidate in rec.candidates]
        command_args = [candidate.command_arg for candidate in rec.candidates]
        self.assertIn("baseline", ids)
        self.assertIn("github:example-lab-writing-skill", ids)
        self.assertIn("https://github.com/example/lab-writing-skill#skills/lab-writing", command_args)
        self.assertEqual(rec.discovery_queries, ["claude skill anti ai writing humanize"])

    def test_recommend_local_only_skips_online_discovery(self):
        from recommend import recommend_candidates

        with patch("recommend.discover_online_skills") as discovery:
            rec = recommend_candidates("我想比较几个写作 skill，只要本地候选。", online_discovery=True)

        discovery.assert_not_called()
        self.assertTrue(any("只用本地" in note for note in rec.notes_zh))


class TestTokenEstimates(unittest.TestCase):
    def test_estimate_run_tokens_counts_skill_prompt(self):
        from compare import estimate_run_tokens
        short = estimate_run_tokens("任务", "short prompt", "output")
        long = estimate_run_tokens("任务", "长提示 " * 1000, "output")
        self.assertGreater(long, short)
        self.assertGreater(long, 500)


class TestVerdictServer(unittest.TestCase):
    def test_imports(self):
        from verdict_server import start_server, wait_for_verdict, stop_server

    def test_start_stop_cycle(self):
        """Server should start on a free port and stop cleanly."""
        from verdict_server import start_server, stop_server
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            log_path = Path(f.name)
            f.write(b'{"verdict": null}')
        try:
            port = start_server(log_path)
            self.assertIsInstance(port, int)
            self.assertGreater(port, 1024)
        finally:
            stop_server()
            log_path.unlink()

    def test_verdict_writes_handoff_file(self):
        """A submitted verdict should update the log and create a copyable handoff."""
        from verdict_server import start_server, stop_server, wait_for_verdict
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "run.json"
            log_path.write_text(json.dumps({
                "timestamp": "test",
                "candidates": [{"id": "humanizer", "name": "Humanizer"}],
                "report_path": "/tmp/report.html",
                "verdict": None,
            }), encoding="utf-8")
            try:
                port = start_server(log_path)
                payload = {
                    "winner": "humanizer",
                    "winner_name": "Humanizer",
                    "verdict_type": "pick",
                    "reason": "more natural",
                    "handoff_text": "Please continue using Humanizer.",
                }
                req = urllib.request.Request(
                    f"http://127.0.0.1:{port}/verdict",
                    data=json.dumps(payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=5) as resp:
                    self.assertEqual(resp.status, 200)

                verdict = wait_for_verdict(timeout_seconds=2)
                self.assertIsNotNone(verdict)
                self.assertIn("handoff_path", verdict)

                updated = json.loads(log_path.read_text(encoding="utf-8"))
                self.assertEqual(updated["verdict"]["winner"], "humanizer")
                handoff_path = Path(updated["handoff_path"])
                self.assertTrue(handoff_path.exists())
                handoff = handoff_path.read_text(encoding="utf-8")
                self.assertIn("Please continue using Humanizer.", handoff)
                self.assertIn("Reason: more natural", handoff)

                latest_log = log_path.parent / "latest.json"
                latest_handoff = log_path.parent / "latest.handoff.md"
                self.assertTrue(latest_log.exists())
                self.assertTrue(latest_handoff.exists())
                latest = json.loads(latest_log.read_text(encoding="utf-8"))
                self.assertEqual(latest["verdict"]["winner"], "humanizer")
                self.assertEqual(latest["source_log_path"], str(log_path.resolve()))
                self.assertIn("Please continue using Humanizer.", latest_handoff.read_text(encoding="utf-8"))
            finally:
                stop_server()


class TestResumeVerdict(unittest.TestCase):
    def test_resume_latest_verdict(self):
        from resume_verdict import build_resume_payload, find_latest_verdict_log
        with tempfile.TemporaryDirectory() as tmp:
            logs_dir = Path(tmp) / "forkprobe-logs"
            logs_dir.mkdir()
            log_path = logs_dir / "run.json"
            log = {
                "timestamp": "test",
                "source_log_path": str(log_path.resolve()),
                "report_path": "/tmp/report.html",
                "candidates": [{"id": "byo:social", "name": "social (BYO)"}],
                "verdict": {
                    "winner": "byo:social",
                    "winner_name": "social (BYO)",
                    "verdict_type": "pick",
                    "reason": "小红书化的风格，更通俗易懂。",
                    "handoff_text": "请继续使用 social (BYO) (byo:social) 继续处理这个任务。",
                },
            }
            log_path.write_text(json.dumps(log, ensure_ascii=False), encoding="utf-8")
            (logs_dir / "latest.json").write_text(json.dumps(log, ensure_ascii=False), encoding="utf-8")

            found_path, loaded = find_latest_verdict_log(logs_dirs=[logs_dir])
            self.assertEqual(found_path, (logs_dir / "latest.json").resolve())
            payload = build_resume_payload(found_path, loaded)
            self.assertEqual(payload["winner"], "byo:social")
            self.assertEqual(payload["winner_name"], "social (BYO)")
            self.assertIn("social (BYO)", payload["handoff_text"])


class TestRenderReport(unittest.TestCase):
    def test_render_with_dummy_data(self):
        from render_report import render
        dummy = [
            {
                "skill_id": "baseline", "skill_name": "Baseline", "skill_author": "—",
                "skill_category": "baseline", "output": "test output", "tokens_used": 10,
                "provider_tokens_used": 10, "estimated_tokens_used": 42,
                "latency_seconds": 1.0, "error": None,
            },
            {
                "skill_id": "test-skill", "skill_name": "Test", "skill_author": "tester",
                "skill_category": "test", "output": "another output", "tokens_used": 20,
                "provider_tokens_used": 20, "estimated_tokens_used": 84,
                "latency_seconds": 2.0, "error": None,
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "r.html"
            render(task_input="test input", results=dummy, duration_seconds=3.0,
                   output_path=out, auto_open=False, verdict_url="http://localhost:1234/verdict",
                   judge_result={
                       "winner_skill_id": "test-skill",
                       "verdict_type": "pick",
                       "confidence": 0.82,
                       "summary": "Test is clearer.",
                       "reasoning": "It preserves the task and reads better.",
                       "scores": {
                           "baseline": {"score": 70, "note": "usable"},
                           "test-skill": {"score": 88, "note": "clearer"},
                       },
                       "tokens_used": 120,
                       "latency_seconds": 2.5,
                       "error": None,
                       "raw_output": "",
                   })
            html = out.read_text()
            self.assertIn("forkprobe", html)
            self.assertIn("function forkprobeReport()", html)
            self.assertIn("flow-graph", html)
            self.assertNotIn("forkprobe Comparison Report", html)
            self.assertIn("test output", html)
            self.assertIn("another output", html)
            self.assertIn("http://localhost:1234/verdict", html)
            self.assertIn("AI judge recommendation", html)
            self.assertIn("Test is clearer.", html)
            self.assertIn("handoffText", html)
            self.assertIn("handoff_text", html)
            self.assertIn("Copy handoff", html)
            self.assertIn("I picked", html)
            self.assertIn("我选好了", html)
            self.assertIn("estimated context", html)
            self.assertIn("includes skill instructions, input, and output", html)
            self.assertIn("model raw count, for reference", html)
            self.assertIn("126", html)
            self.assertIn("total tokens", html)
            self.assertIn("navigator.languages", html)
            self.assertIn("setLang('zh')", html)
            self.assertIn("✓ 选择这个", html)
            self.assertIn("results-scroll", html)
            self.assertIn("result-card", html)

    def test_render_artifact_manifest(self):
        from render_artifact_report import render_from_manifest
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pptx = tmp_path / "candidate.pptx"
            preview = tmp_path / "candidate.png"
            pptx.write_bytes(b"fake pptx")
            preview.write_bytes(b"fake png")
            manifest = tmp_path / "manifest.json"
            manifest.write_text(json.dumps({
                "task_input": "make a PPT",
                "candidates": [
                    {
                        "id": "baseline-presentations",
                        "name": "baseline + presentations",
                        "summary": "Generated a direct PPTX baseline.",
                        "artifacts": [
                            {
                                "path": str(pptx),
                                "preview_path": str(preview),
                                "label": "candidate.pptx",
                                "kind": "PPTX",
                            }
                        ],
                    }
                ],
            }), encoding="utf-8")
            out = tmp_path / "artifact.html"
            render_from_manifest(manifest, out, auto_open=False)
            html = out.read_text(encoding="utf-8")
            self.assertIn("Generated artifacts", html)
            self.assertIn("candidate.pptx", html)
            self.assertIn("baseline-presentations", html)


class TestJudgeParsing(unittest.TestCase):
    def _results(self):
        from compare import RunResult
        return [
            RunResult(
                skill_id="baseline",
                skill_name="Baseline",
                skill_author="—",
                skill_category="baseline",
                output="Plain output.",
                tokens_used=10,
                latency_seconds=1.0,
                error=None,
            ),
            RunResult(
                skill_id="humanizer",
                skill_name="Humanizer",
                skill_author="tester",
                skill_category="anti-AI",
                output="More natural output.",
                tokens_used=20,
                latency_seconds=2.0,
                error=None,
            ),
        ]

    def test_parse_judge_json_with_fence(self):
        from compare import parse_judge_output
        output = """```json
{
  "winner_skill_id": "humanizer",
  "verdict_type": "pick",
  "confidence": 1.2,
  "summary": "Humanizer is stronger.",
  "reasoning": "It is more specific and readable.",
  "scores": {
    "baseline": {"score": 72, "note": "clear but generic"},
    "humanizer": {"score": 90, "note": "more natural"}
  }
}
```"""
        judge = parse_judge_output(output, self._results(), tokens=100, latency=3.0)
        self.assertIsNone(judge.error)
        self.assertEqual(judge.winner_skill_id, "humanizer")
        self.assertEqual(judge.verdict_type, "pick")
        self.assertEqual(judge.confidence, 1.0)
        self.assertIn("humanizer", judge.scores)

    def test_parse_bad_judge_output(self):
        from compare import parse_judge_output
        judge = parse_judge_output("not json", self._results(), tokens=5, latency=0.2)
        self.assertIsNotNone(judge.error)
        self.assertEqual(judge.winner_skill_id, None)


if __name__ == "__main__":
    unittest.main(verbosity=2)
