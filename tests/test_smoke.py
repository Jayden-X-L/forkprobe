"""
Smoke tests for forkprobe. Run with: python3 tests/test_smoke.py

These cover the non-network logic: imports, catalog loading, skill parsing,
report rendering, log writing. They do NOT call the live LLM API (that's the
integration test job — see tests/test_integration.py).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
import urllib.error
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

    def test_remote_skill_source_validation(self):
        from skill_loader import _normalize_remote_skill_url, fetch_skill

        self.assertEqual(
            _normalize_remote_skill_url("https://github.com/example/repo/tree/main/skills/demo"),
            "https://github.com/example/repo.git",
        )
        self.assertEqual(
            _normalize_remote_skill_url("https://gitlab.com/example/repo"),
            "https://gitlab.com/example/repo.git",
        )
        old_allow = os.environ.pop("FORKPROBE_ALLOW_UNTRUSTED_SKILL_SOURCE", None)
        try:
            for source in (
                "http://github.com/example/repo",
                "https://127.0.0.1/example/repo",
                "https://localhost/example/repo",
                "https://example.com/not-allowed/repo",
            ):
                with self.subTest(source=source):
                    with self.assertRaises(ValueError):
                        _normalize_remote_skill_url(source)

            with self.assertRaises(ValueError):
                fetch_skill("git@github.com:example/repo.git")
        finally:
            if old_allow is not None:
                os.environ["FORKPROBE_ALLOW_UNTRUSTED_SKILL_SOURCE"] = old_allow


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

    def test_recommend_figure_text_only_scope(self):
        from recommend import recommend_candidates
        rec = recommend_candidates("我只要比较科研图的图注和 figure storyline，不生成图。", online_discovery=False)
        self.assertEqual(rec.deliverable_type, "text")
        self.assertEqual(rec.compare_mode, "text")
        command_args = [c.command_arg for c in rec.candidates]
        self.assertIn("https://github.com/Yuan1z0825/nature-skills#skills/nature-figure", command_args)
        self.assertTrue(rec.notes_zh)

    def test_recommend_scientific_figure_routes_to_artifact_mode(self):
        from recommend import format_text, recommend_candidates
        rec = recommend_candidates("请比较几个 pipeline，生成论文机制图成品，最终要 PNG、SVG、PDF、caption 和源代码。", online_discovery=False)
        self.assertEqual(rec.deliverable_type, "visual_artifact")
        self.assertEqual(rec.compare_mode, "artifact")
        ids = [c.id for c in rec.candidates]
        self.assertIn("baseline-python-figure", ids)
        self.assertIn("nature-figure-python", ids)
        self.assertIn("schematic-svg", ids)
        self.assertIn("scripts/figure_artifact.py", rec.suggested_command)
        self.assertIn("--judge", rec.suggested_command)
        text = format_text(rec, input_path="figure-task.md", lang="zh")
        self.assertIn("figure_artifact.py", text)
        self.assertIn("PNG 预览", "\n".join(rec.notes_zh))

    def test_recommend_visual_artifact_includes_external_skill_source_command(self):
        from discover_skills import OnlineDiscoveryReport, OnlineSkillCandidate
        from recommend import recommend_candidates

        fake_report = OnlineDiscoveryReport(
            deliverable="visual_artifact",
            queries=["claude skill scientific figure schematic"],
            candidates=[
                OnlineSkillCandidate(
                    id="github:example-figure-skill",
                    name="example-figure-skill",
                    source="https://github.com/example/figure-skill",
                    command_arg="https://github.com/example/figure-skill#skills/scientific-figure",
                    summary_zh="GitHub 发现的科研绘图 skill。",
                    summary_en="GitHub-discovered scientific figure skill.",
                    score=91,
                    stars=88,
                    category="github_discovered",
                    skill_path="skills/scientific-figure/SKILL.md",
                )
            ],
            notes_zh=["已发现外部科研绘图候选。"],
            notes_en=["Found external scientific figure candidate."],
        )
        with patch("recommend.discover_online_skills", return_value=fake_report):
            rec = recommend_candidates("请比较几个 skill 生成论文机制图成品，输出 SVG 和 caption。", online_discovery=True)

        self.assertEqual(rec.deliverable_type, "visual_artifact")
        self.assertIn("--pipeline", rec.suggested_command)
        self.assertIn("--skill-source", rec.suggested_command)
        self.assertIn("https://github.com/example/figure-skill#skills/scientific-figure", rec.suggested_command)
        self.assertEqual(rec.discovery_queries, ["claude skill scientific figure schematic"])

    def test_recommend_research_report_routes_to_artifact_mode(self):
        from recommend import format_text, recommend_candidates

        rec = recommend_candidates(
            "请并行比较几个调研报告 skill，生成一份 AI 教育市场调研报告，要求 sources.json 和 evidence table。",
            online_discovery=False,
        )
        self.assertEqual(rec.deliverable_type, "research_report")
        self.assertEqual(rec.compare_mode, "artifact")
        ids = [c.id for c in rec.candidates]
        self.assertIn("baseline-research-report", ids)
        self.assertIn("source-first-research", ids)
        self.assertIn("evidence-table-report", ids)
        self.assertIn("scripts/research_artifact.py", rec.suggested_command)
        self.assertIn("--judge", rec.suggested_command)
        text = format_text(rec, input_path="research-task.md", lang="zh")
        self.assertIn("research_artifact.py", text)
        self.assertIn("调研报告 pipeline", text)
        self.assertIn("sources.json", "\n".join(rec.notes_zh))

    def test_recommend_user_research_report_includes_cookiy_pipeline(self):
        from recommend import recommend_candidates

        rec = recommend_candidates(
            "请比较几个用户研究 skill，基于访谈和问卷材料生成用户调研报告。",
            online_discovery=False,
        )
        self.assertEqual(rec.deliverable_type, "research_report")
        ids = [c.id for c in rec.candidates]
        self.assertIn("user-research-cookiy-report", ids)
        command_args = [c.command_arg for c in rec.candidates]
        self.assertIn("https://github.com/cookiy-ai/user-research-skill", command_args)

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
        from verdict_server import build_verdict_url, start_server, wait_for_verdict, stop_server

    def test_start_stop_cycle(self):
        """Server should start on a free port and stop cleanly."""
        from verdict_server import build_verdict_url, start_server, stop_server
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            log_path = Path(f.name)
            f.write(b'{"verdict": null}')
        try:
            port = start_server(log_path)
            self.assertIsInstance(port, int)
            self.assertGreater(port, 1024)
            self.assertIn("token=", build_verdict_url(port))
        finally:
            stop_server()
            log_path.unlink()

    def test_verdict_writes_handoff_file(self):
        """A submitted verdict should update the log and create a copyable handoff."""
        from verdict_server import build_verdict_url, start_server, stop_server, wait_for_verdict
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
                verdict_url = build_verdict_url(port)
                payload = {
                    "winner": "humanizer",
                    "winner_name": "Humanizer",
                    "verdict_type": "pick",
                    "reason": "more natural",
                    "handoff_text": "Please continue using Humanizer.",
                }
                bad_req = urllib.request.Request(
                    f"http://localhost:{port}/verdict",
                    data=json.dumps(payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(urllib.error.HTTPError) as cm:
                    urllib.request.urlopen(bad_req, timeout=5)
                self.assertEqual(cm.exception.code, 403)
                cm.exception.close()

                req = urllib.request.Request(
                    verdict_url,
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
            verdict_url = "http://localhost:1234/verdict?token=test"
            render(task_input="test input", results=dummy, duration_seconds=3.0,
                   output_path=out, auto_open=False, verdict_url=verdict_url,
                   judge_result={
                       "winner_skill_id": "test-skill",
                       "verdict_type": "pick",
                       "confidence": 0.82,
                       "summary": "Test is clearer.",
                       "reasoning": "It preserves the task and reads better.",
                       "scores": {
                           "baseline": {"score": 7, "note": "usable"},
                           "test-skill": {"score": 9, "note": "clearer"},
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
            self.assertIn(verdict_url, html)
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
            self.assertIn("9/10", html)
            self.assertNotIn("/100", html)
            self.assertIn('href="https://github.com/Jayden-X-L/forkprobe"', html)
            self.assertIn('target="_blank"', html)
            self.assertNotIn('href="github"', html)

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

    def test_render_artifact_manifest_shows_artifacts_when_candidate_has_error(self):
        from render_artifact_report import render_from_manifest
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            preview = tmp_path / "preview.png"
            svg = tmp_path / "figure.svg"
            preview.write_bytes(b"fake png")
            svg.write_text("<svg></svg>", encoding="utf-8")
            manifest = tmp_path / "manifest.json"
            manifest.write_text(json.dumps({
                "task_input": "make a figure",
                "candidates": [
                    {
                        "id": "schematic-svg",
                        "name": "schematic SVG",
                        "summary": "Generated a partial figure package.",
                        "error": "Codex CLI timeout after 240s.",
                        "artifacts": [
                            {
                                "path": str(svg),
                                "preview_path": str(preview),
                                "label": "figure.svg",
                                "kind": "SVG",
                            }
                        ],
                    }
                ],
            }), encoding="utf-8")
            out = tmp_path / "artifact.html"
            render_from_manifest(manifest, out, auto_open=False)
            html = out.read_text(encoding="utf-8")
            self.assertIn("artifacts available", html)
            self.assertIn("Codex CLI timeout after 240s.", html)
            self.assertIn("Generated artifacts", html)
            self.assertIn("figure.svg", html)
            self.assertIn("Generated a partial figure package.", html)

    def test_prepare_figure_artifact_workspace_and_report(self):
        from figure_artifact import create_workspace
        from render_artifact_report import render_from_manifest

        task = "请基于实验数据做一个科研 plot 成品，输出 PNG、SVG、PDF、caption 和 QA。"
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            result = create_workspace(task_input=task, output_dir=tmp_path)
            self.assertEqual(result["figure_type"], "plot")
            self.assertIn("plot-code-python", result["pipelines"])

            plot_dir = tmp_path / "candidates" / "plot-code-python"
            artifact_dir = plot_dir / "artifacts"
            (artifact_dir / "preview.png").write_bytes(b"fake png")
            (artifact_dir / "figure.svg").write_text("<svg></svg>", encoding="utf-8")
            (artifact_dir / "figure.pdf").write_bytes(b"%PDF-1.4")
            (artifact_dir / "source.py").write_text("print('plot')\n", encoding="utf-8")
            (artifact_dir / "caption.md").write_text("Figure 1. A reproducible data plot.", encoding="utf-8")
            (artifact_dir / "qa.md").write_text("Labels readable; exports present.", encoding="utf-8")

            refreshed = create_workspace(
                task_input=task,
                output_dir=tmp_path,
                pipeline_ids=result["pipelines"],
            )
            manifest_path = Path(refreshed["manifest_path"])
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            plot_candidate = next(c for c in manifest["candidates"] if c["id"] == "plot-code-python")
            labels = {artifact["label"] for artifact in plot_candidate["artifacts"]}
            self.assertIn("preview.png", labels)
            self.assertIn("figure.svg", labels)
            self.assertIn("figure.pdf", labels)
            self.assertIn("source.py", labels)
            self.assertIn("Figure 1", plot_candidate["summary"])
            self.assertGreater(plot_candidate["estimated_tokens_used"], 0)
            self.assertEqual(plot_candidate["provider_tokens_used"], 0)

            out = tmp_path / "figure-report.html"
            render_from_manifest(manifest_path, out, auto_open=False)
            html = out.read_text(encoding="utf-8")
            self.assertIn("Generated artifacts", html)
            self.assertIn("figure.svg", html)
            self.assertIn("plot-code-python", html)
            self.assertIn("Figure 1", html)

    def test_figure_run_prompt_embeds_local_skill_prompt(self):
        from figure_artifact import FigurePipeline, build_candidate_run_prompt, build_pipeline_instructions

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill_dir = root / "skills" / "demo-figure"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                "---\n"
                "name: Demo figure skill\n"
                "description: Demo scientific figure instructions\n"
                "---\n"
                "Use exact panel labels and write a concise scientific caption.\n",
                encoding="utf-8",
            )
            candidate_dir = root / "candidate"
            (candidate_dir / "artifacts").mkdir(parents=True)
            pipeline = FigurePipeline(
                id="demo-figure-pipeline",
                name="demo figure pipeline",
                role="test",
                summary_zh="测试 pipeline。",
                summary_en="Test pipeline.",
                pipeline_steps=["demo-skill", "svg-render"],
                best_for=["schematic"],
                expected_artifacts=["preview.png", "figure.svg", "caption.md"],
                qa_checks=["labels_readable"],
                skill_source=f"{root}#skills/demo-figure",
            )
            task = "请生成一张机制图。"
            (candidate_dir / "INSTRUCTIONS.md").write_text(
                build_pipeline_instructions(task, pipeline, candidate_dir),
                encoding="utf-8",
            )

            old = os.environ.get("FORKPROBE_FIGURE_LOAD_SKILL_PROMPTS")
            os.environ["FORKPROBE_FIGURE_LOAD_SKILL_PROMPTS"] = "1"
            try:
                prompt = build_candidate_run_prompt(task, pipeline, candidate_dir)
            finally:
                if old is None:
                    os.environ.pop("FORKPROBE_FIGURE_LOAD_SKILL_PROMPTS", None)
                else:
                    os.environ["FORKPROBE_FIGURE_LOAD_SKILL_PROMPTS"] = old

            self.assertIn("External Skill Instructions", prompt)
            self.assertIn("Demo figure skill", prompt)
            self.assertIn("Use exact panel labels", prompt)
            self.assertIn("skills/demo-figure", prompt)

    def test_prepare_figure_artifact_workspace_with_byo_skill_source(self):
        from figure_artifact import create_workspace
        from render_artifact_report import render_from_manifest

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill_dir = root / "skills" / "demo-figure"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                "---\n"
                "name: demo-figure\n"
                "description: Demo figure skill\n"
                "---\n"
                "Create clean mechanism diagrams.\n",
                encoding="utf-8",
            )
            task = "请比较外部 skill 生成论文机制图成品。"
            result = create_workspace(
                task_input=task,
                output_dir=root / "run",
                pipeline_ids=["baseline-python-figure"],
                skill_sources=[f"{root}#skills/demo-figure"],
            )
            self.assertIn("baseline-python-figure", result["pipelines"])
            dynamic_ids = [pipeline_id for pipeline_id in result["pipelines"] if pipeline_id.startswith("skill-")]
            self.assertEqual(dynamic_ids, ["skill-demo-figure"])

            candidate_dir = Path(result["output_dir"]) / "candidates" / "skill-demo-figure"
            instructions = (candidate_dir / "INSTRUCTIONS.md").read_text(encoding="utf-8")
            self.assertIn("External skill source", instructions)
            self.assertIn("skills/demo-figure", instructions)

            artifact_dir = candidate_dir / "artifacts"
            (artifact_dir / "figure.svg").write_text("<svg></svg>", encoding="utf-8")
            (artifact_dir / "caption.md").write_text("Demo caption.", encoding="utf-8")
            refreshed = create_workspace(
                task_input=task,
                output_dir=Path(result["output_dir"]),
                pipeline_ids=result["pipelines"],
                skill_sources=[f"{root}#skills/demo-figure"],
            )
            manifest = json.loads(Path(refreshed["manifest_path"]).read_text(encoding="utf-8"))
            candidate = next(c for c in manifest["candidates"] if c["id"] == "skill-demo-figure")
            self.assertEqual(candidate["skill_source"], f"{root}#skills/demo-figure")
            self.assertIn("Demo caption.", candidate["summary"])
            self.assertIn("figure.svg", {artifact["label"] for artifact in candidate["artifacts"]})

            out = root / "report.html"
            render_from_manifest(Path(refreshed["manifest_path"]), out, auto_open=False)
            html = out.read_text(encoding="utf-8")
            self.assertIn("skill-demo-figure", html)
            self.assertIn("Demo caption.", html)

    def test_build_artifact_judge_results_includes_files_caption_and_qa(self):
        from figure_artifact import build_artifact_judge_results

        manifest = {
            "candidates": [
                {
                    "id": "skill-demo-figure",
                    "name": "demo figure",
                    "summary": "Summary text.\n\n## Caption\nA caption.\n\n## QA\nQA passed.",
                    "category": "figure-artifact",
                    "expected_artifacts": ["preview.png", "figure.svg", "caption.md", "qa.md"],
                    "qa_checks": ["caption_matches_visual"],
                    "artifacts": [
                        {"label": "figure.svg", "kind": "SVG", "preview_path": "figure.svg"},
                        {"label": "caption.md", "kind": "MD"},
                    ],
                    "tokens_used": 12,
                    "latency_seconds": 1.5,
                    "error": "Codex CLI timeout after 240s. Partial artifacts are available.",
                }
            ]
        }
        results = build_artifact_judge_results(manifest)
        self.assertEqual(results[0].skill_id, "skill-demo-figure")
        self.assertIn("Summary text.", results[0].output)
        self.assertIn("A caption.", results[0].output)
        self.assertIn("figure.svg", results[0].output)
        self.assertIn("caption_matches_visual", results[0].output)
        self.assertIn("Runner issue", results[0].output)
        self.assertIsNone(results[0].error)

    def test_run_figure_pipeline_with_fake_codex(self):
        from figure_artifact import create_workspace, run_parallel
        from render_artifact_report import render_from_manifest

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fake_cli = tmp_path / "codex"
            fake_cli.write_text(
                "#!/usr/bin/env python3\n"
                "import pathlib, re, sys\n"
                "args = sys.argv[1:]\n"
                "out = pathlib.Path(args[args.index('--output-last-message') + 1])\n"
                "prompt = sys.stdin.read()\n"
                "match = re.search(r'generated files under\\s*`([^`]+)`', prompt) or re.search(r'candidate outputs under:\\s*`([^`]+)`', prompt)\n"
                "artifact_dir = pathlib.Path(match.group(1))\n"
                "artifact_dir.mkdir(parents=True, exist_ok=True)\n"
                "(artifact_dir / 'preview.png').write_bytes(b'fake png')\n"
                "(artifact_dir / 'figure.svg').write_text('<svg></svg>', encoding='utf-8')\n"
                "(artifact_dir / 'caption.md').write_text('Fake caption.', encoding='utf-8')\n"
                "(artifact_dir / 'qa.md').write_text('Fake QA passed.', encoding='utf-8')\n"
                "summary = artifact_dir.parent / 'summary.md'\n"
                "summary.write_text('Fake runner summary.', encoding='utf-8')\n"
                "out.write_text('Fake runner completed.', encoding='utf-8')\n"
                "print('tokens used\\n1,111', file=sys.stderr)\n",
                encoding="utf-8",
            )
            os.chmod(fake_cli, 0o755)

            old_cli = os.environ.get("FORKPROBE_CODEX_CLI")
            old_sandbox = os.environ.get("FORKPROBE_FIGURE_SANDBOX")
            os.environ["FORKPROBE_CODEX_CLI"] = str(fake_cli)
            os.environ["FORKPROBE_FIGURE_SANDBOX"] = "workspace-write"
            try:
                task = "请生成一张论文机制图成品，输出 PNG、SVG 和 caption。"
                result = create_workspace(
                    task_input=task,
                    output_dir=tmp_path / "run",
                    pipeline_ids=["schematic-svg"],
                )
                runs = run_parallel(
                    task_input=task,
                    output_dir=Path(result["output_dir"]),
                    pipeline_ids=["schematic-svg"],
                    max_workers=1,
                    timeout=30,
                )
                self.assertIsNone(runs[0].error)
                self.assertEqual(runs[0].tokens_used, 1111)

                refreshed = create_workspace(
                    task_input=task,
                    output_dir=Path(result["output_dir"]),
                    pipeline_ids=["schematic-svg"],
                )
                manifest_path = Path(refreshed["manifest_path"])
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                candidate = manifest["candidates"][0]
                labels = {artifact["label"] for artifact in candidate["artifacts"]}
                self.assertIn("preview.png", labels)
                self.assertIn("figure.svg", labels)
                self.assertEqual(candidate["tokens_used"], 1111)
                self.assertIn("Fake runner summary.", candidate["summary"])
                self.assertIn("Fake runner completed.", candidate["summary"])

                out = tmp_path / "figure-run-report.html"
                render_from_manifest(manifest_path, out, auto_open=False)
                html = out.read_text(encoding="utf-8")
                self.assertIn("Fake runner summary.", html)
                self.assertIn("figure.svg", html)
            finally:
                if old_cli is None:
                    os.environ.pop("FORKPROBE_CODEX_CLI", None)
                else:
                    os.environ["FORKPROBE_CODEX_CLI"] = old_cli
                if old_sandbox is None:
                    os.environ.pop("FORKPROBE_FIGURE_SANDBOX", None)
                else:
                    os.environ["FORKPROBE_FIGURE_SANDBOX"] = old_sandbox

    def test_figure_pipeline_timeout_keeps_partial_artifacts(self):
        from figure_artifact import create_workspace, run_parallel

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fake_cli = tmp_path / "codex"
            fake_cli.write_text(
                "#!/usr/bin/env python3\n"
                "import pathlib, re, sys, time\n"
                "prompt = sys.stdin.read()\n"
                "match = re.search(r'generated files under\\s*`([^`]+)`', prompt) or re.search(r'candidate outputs under:\\s*`([^`]+)`', prompt)\n"
                "artifact_dir = pathlib.Path(match.group(1))\n"
                "artifact_dir.mkdir(parents=True, exist_ok=True)\n"
                "(artifact_dir / 'figure.svg').write_text('<svg></svg>', encoding='utf-8')\n"
                "time.sleep(5)\n",
                encoding="utf-8",
            )
            os.chmod(fake_cli, 0o755)

            old_cli = os.environ.get("FORKPROBE_CODEX_CLI")
            os.environ["FORKPROBE_CODEX_CLI"] = str(fake_cli)
            try:
                task = "请生成一张论文机制图成品，输出 SVG。"
                result = create_workspace(
                    task_input=task,
                    output_dir=tmp_path / "run",
                    pipeline_ids=["schematic-svg"],
                )
                runs = run_parallel(
                    task_input=task,
                    output_dir=Path(result["output_dir"]),
                    pipeline_ids=["schematic-svg"],
                    max_workers=1,
                    timeout=1,
                )
                self.assertIsNotNone(runs[0].error)
                self.assertIn("Partial artifacts are available", runs[0].error)

                refreshed = create_workspace(
                    task_input=task,
                    output_dir=Path(result["output_dir"]),
                    pipeline_ids=["schematic-svg"],
                )
                manifest = json.loads(Path(refreshed["manifest_path"]).read_text(encoding="utf-8"))
                labels = {artifact["label"] for artifact in manifest["candidates"][0]["artifacts"]}
                self.assertIn("figure.svg", labels)
            finally:
                if old_cli is None:
                    os.environ.pop("FORKPROBE_CODEX_CLI", None)
                else:
                    os.environ["FORKPROBE_CODEX_CLI"] = old_cli

    def test_figure_artifact_cli_json_run_is_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fake_cli = tmp_path / "codex"
            fake_cli.write_text(
                "#!/usr/bin/env python3\n"
                "import pathlib, re, sys\n"
                "args = sys.argv[1:]\n"
                "out = pathlib.Path(args[args.index('--output-last-message') + 1])\n"
                "prompt = sys.stdin.read()\n"
                "match = re.search(r'generated files under\\s*`([^`]+)`', prompt) or re.search(r'candidate outputs under:\\s*`([^`]+)`', prompt)\n"
                "artifact_dir = pathlib.Path(match.group(1))\n"
                "artifact_dir.mkdir(parents=True, exist_ok=True)\n"
                "(artifact_dir / 'preview.png').write_bytes(b'fake png')\n"
                "(artifact_dir / 'figure.svg').write_text('<svg></svg>', encoding='utf-8')\n"
                "out.write_text('CLI fake completed.', encoding='utf-8')\n"
                "print('tokens used\\n2,222', file=sys.stderr)\n",
                encoding="utf-8",
            )
            os.chmod(fake_cli, 0o755)
            env = dict(os.environ)
            env["FORKPROBE_CODEX_CLI"] = str(fake_cli)
            proc = subprocess.run(
                [
                    sys.executable,
                    str(PROJECT_DIR / "scripts" / "figure_artifact.py"),
                    "--text",
                    "请生成一张论文机制图成品，输出 PNG 和 SVG。",
                    "--output-dir",
                    str(tmp_path / "run"),
                    "--pipeline",
                    "schematic-svg",
                    "--run",
                    "--render-report",
                    "--no-open",
                    "--json",
                ],
                text=True,
                capture_output=True,
                check=True,
                env=env,
            )
            payload = json.loads(proc.stdout)
            self.assertIn("figure pipeline schematic-svg: ok", proc.stderr)
            self.assertTrue(Path(payload["report_path"]).exists())
            manifest = json.loads(Path(payload["manifest_path"]).read_text(encoding="utf-8"))
            candidate = manifest["candidates"][0]
            self.assertEqual(candidate["tokens_used"], 2222)
            self.assertIn("figure.svg", {artifact["label"] for artifact in candidate["artifacts"]})

    def test_figure_artifact_cli_accepts_skill_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            skill_dir = tmp_path / "skills" / "demo-figure"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                "---\n"
                "name: demo-figure\n"
                "description: Demo figure skill\n"
                "---\n"
                "Render a concise figure package.\n",
                encoding="utf-8",
            )
            proc = subprocess.run(
                [
                    sys.executable,
                    str(PROJECT_DIR / "scripts" / "figure_artifact.py"),
                    "--text",
                    "请生成一张论文机制图成品。",
                    "--output-dir",
                    str(tmp_path / "run"),
                    "--pipeline",
                    "baseline-python-figure",
                    "--skill-source",
                    f"{tmp_path}#skills/demo-figure",
                    "--json",
                ],
                text=True,
                capture_output=True,
                check=True,
            )
            payload = json.loads(proc.stdout)
            self.assertIn("skill-demo-figure", payload["pipelines"])
            self.assertIn(f"{tmp_path}#skills/demo-figure", payload["skill_sources"])

    def test_prepare_research_artifact_workspace_and_report(self):
        from render_artifact_report import render_from_manifest
        from research_artifact import create_workspace

        task = "请生成一份 AI 教育行业调研报告，输出 sources.json、evidence table、claim checks 和 limitations。"
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            result = create_workspace(task_input=task, output_dir=tmp_path)
            self.assertEqual(result["research_type"], "market")
            self.assertIn("source-first-research", result["pipelines"])

            candidate_dir = tmp_path / "candidates" / "source-first-research"
            artifact_dir = candidate_dir / "artifacts"
            (artifact_dir / "candidate-report.md").write_text("# AI education market\n\nClear report.", encoding="utf-8")
            (artifact_dir / "candidate-report.html").write_text("<h1>AI education market</h1>", encoding="utf-8")
            (artifact_dir / "sources.json").write_text(json.dumps([{"title": "Source", "url": "https://example.com"}]), encoding="utf-8")
            (artifact_dir / "evidence-table.md").write_text("| Claim | Evidence |\n|---|---|\n| A | B |\n", encoding="utf-8")
            (artifact_dir / "claim-checks.md").write_text("No unsupported claims found.", encoding="utf-8")
            (artifact_dir / "limitations.md").write_text("Needs fresher market size data.", encoding="utf-8")

            refreshed = create_workspace(
                task_input=task,
                output_dir=tmp_path,
                pipeline_ids=result["pipelines"],
            )
            manifest_path = Path(refreshed["manifest_path"])
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            candidate = next(c for c in manifest["candidates"] if c["id"] == "source-first-research")
            labels = {artifact["label"] for artifact in candidate["artifacts"]}
            self.assertIn("candidate-report.md", labels)
            self.assertIn("sources.json", labels)
            self.assertIn("evidence-table.md", labels)
            self.assertIn("AI education market", candidate["summary"])
            self.assertGreater(candidate["estimated_tokens_used"], 0)
            self.assertEqual(candidate["provider_tokens_used"], 0)

            out = tmp_path / "research-report.html"
            render_from_manifest(manifest_path, out, auto_open=False)
            html = out.read_text(encoding="utf-8")
            self.assertIn("Generated artifacts", html)
            self.assertIn("source-first-research", html)
            self.assertIn("sources.json", html)
            self.assertIn("AI education market", html)

    def test_run_research_pipeline_with_fake_codex(self):
        from render_artifact_report import render_from_manifest
        from research_artifact import create_workspace, run_parallel

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fake_cli = tmp_path / "codex"
            fake_cli.write_text(
                "#!/usr/bin/env python3\n"
                "import json, pathlib, re, sys\n"
                "args = sys.argv[1:]\n"
                "out = pathlib.Path(args[args.index('--output-last-message') + 1])\n"
                "prompt = sys.stdin.read()\n"
                "match = re.search(r'candidate outputs under:\\s*\\n\\n`([^`]+)`', prompt)\n"
                "artifact_dir = pathlib.Path(match.group(1))\n"
                "artifact_dir.mkdir(parents=True, exist_ok=True)\n"
                "(artifact_dir / 'candidate-report.md').write_text('# Fake research report\\n\\nEvidence-backed finding.', encoding='utf-8')\n"
                "(artifact_dir / 'candidate-report.html').write_text('<h1>Fake research report</h1>', encoding='utf-8')\n"
                "(artifact_dir / 'sources.json').write_text(json.dumps([{'title': 'Fake source', 'url': 'https://example.com'}]), encoding='utf-8')\n"
                "(artifact_dir / 'evidence-table.md').write_text('| Claim | Evidence |\\n|---|---|\\n| A | B |\\n', encoding='utf-8')\n"
                "(artifact_dir / 'claim-checks.md').write_text('Fake claim checks.', encoding='utf-8')\n"
                "(artifact_dir / 'limitations.md').write_text('Fake limitations.', encoding='utf-8')\n"
                "summary = artifact_dir.parent / 'summary.md'\n"
                "summary.write_text('Fake research runner summary.', encoding='utf-8')\n"
                "out.write_text('Fake research runner completed.', encoding='utf-8')\n"
                "print('tokens used\\n3,333', file=sys.stderr)\n",
                encoding="utf-8",
            )
            os.chmod(fake_cli, 0o755)

            old_cli = os.environ.get("FORKPROBE_CODEX_CLI")
            old_sandbox = os.environ.get("FORKPROBE_RESEARCH_SANDBOX")
            os.environ["FORKPROBE_CODEX_CLI"] = str(fake_cli)
            os.environ["FORKPROBE_RESEARCH_SANDBOX"] = "workspace-write"
            try:
                task = "请生成一份 AI 教育市场调研报告。"
                result = create_workspace(
                    task_input=task,
                    output_dir=tmp_path / "run",
                    pipeline_ids=["source-first-research"],
                )
                runs = run_parallel(
                    task_input=task,
                    output_dir=Path(result["output_dir"]),
                    pipeline_ids=["source-first-research"],
                    max_workers=1,
                    timeout=30,
                )
                self.assertIsNone(runs[0].error)
                self.assertEqual(runs[0].tokens_used, 3333)

                refreshed = create_workspace(
                    task_input=task,
                    output_dir=Path(result["output_dir"]),
                    pipeline_ids=["source-first-research"],
                )
                manifest_path = Path(refreshed["manifest_path"])
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                candidate = manifest["candidates"][0]
                labels = {artifact["label"] for artifact in candidate["artifacts"]}
                self.assertIn("candidate-report.md", labels)
                self.assertIn("sources.json", labels)
                self.assertEqual(candidate["tokens_used"], 3333)
                self.assertIn("Fake research runner summary.", candidate["summary"])

                out = tmp_path / "research-run-report.html"
                render_from_manifest(manifest_path, out, auto_open=False)
                html = out.read_text(encoding="utf-8")
                self.assertIn("Fake research runner summary.", html)
                self.assertIn("sources.json", html)
            finally:
                if old_cli is None:
                    os.environ.pop("FORKPROBE_CODEX_CLI", None)
                else:
                    os.environ["FORKPROBE_CODEX_CLI"] = old_cli
                if old_sandbox is None:
                    os.environ.pop("FORKPROBE_RESEARCH_SANDBOX", None)
                else:
                    os.environ["FORKPROBE_RESEARCH_SANDBOX"] = old_sandbox


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
