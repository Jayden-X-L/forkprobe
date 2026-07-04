"""
Prepare a research-report artifact comparison run.

This artifact mode is for deliverables where the user wants a finished research
report, not a short answer. Each candidate writes a research package with a
report, sources, evidence table, claim checks, limitations, and a summary. The
shared artifact report renderer then compares the packages side by side.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).parent
PROJECT_DIR = SCRIPT_DIR.parent
DEFAULT_OUTPUT_ROOT = PROJECT_DIR / "outputs" / "research-runs"
FALSE_VALUES = {"0", "false", "no", "off"}
ARTIFACT_SUFFIXES = {
    ".md", ".html", ".json", ".csv", ".tsv", ".txt", ".pdf", ".docx", ".xlsx"
}
PREVIEW_SUFFIXES = {".html", ".pdf"}

COMPANY_RESEARCH_SOURCE = "https://github.com/deanpeters/Product-Manager-Skills#skills/company-research"
USER_RESEARCH_SOURCE = "https://github.com/cookiy-ai/user-research-skill"
LITERATURE_REVIEW_SOURCE = "https://github.com/davila7/claude-code-templates#cli-tool/components/skills/scientific/literature-review"
INVESTMENT_RESEARCH_SOURCE = "https://github.com/CaiJichang212/investment-research"


@dataclass(frozen=True)
class ResearchPipeline:
    id: str
    name: str
    role: str
    summary_zh: str
    summary_en: str
    pipeline_steps: list[str]
    best_for: list[str]
    expected_artifacts: list[str]
    qa_checks: list[str]
    skill_source: str = ""


@dataclass(frozen=True)
class ResearchRunResult:
    pipeline_id: str
    output: str
    tokens_used: int
    latency_seconds: float
    error: str | None = None


RESEARCH_PIPELINES: dict[str, ResearchPipeline] = {
    "baseline-research-report": ResearchPipeline(
        id="baseline-research-report",
        name="baseline + research report package",
        role="baseline_research_report",
        summary_zh="不使用专门调研 skill，直接生成完整调研报告包，作为成品基线。",
        summary_en="No specialized research skill; produces a complete research report package as the baseline.",
        pipeline_steps=["baseline", "research-report", "source-and-claim-qa"],
        best_for=["market", "industry", "company", "user", "literature", "investment", "general"],
        expected_artifacts=["candidate-report.md", "candidate-report.html", "sources.json", "evidence-table.md", "claim-checks.md", "limitations.md", "summary.md"],
        qa_checks=["source_backed_claims", "clear_structure", "evidence_table_present", "limitations_explicit"],
    ),
    "source-first-research": ResearchPipeline(
        id="source-first-research",
        name="source-first research report",
        role="source_first_research",
        summary_zh="先收集和筛选来源，再从证据表生成调研报告，强调引用可靠性和可追溯结论。",
        summary_en="Collects and screens sources first, then builds the report from an evidence table with traceable claims.",
        pipeline_steps=["source-discovery", "evidence-table", "report-synthesis", "claim-qa"],
        best_for=["market", "industry", "company", "investment", "general"],
        expected_artifacts=["candidate-report.md", "candidate-report.html", "sources.json", "evidence-table.md", "claim-checks.md", "limitations.md", "summary.md"],
        qa_checks=["primary_sources_preferred", "claims_have_sources", "stale_risk_marked", "limitations_explicit"],
    ),
    "analyst-style-report": ResearchPipeline(
        id="analyst-style-report",
        name="analyst-style research report",
        role="analyst_style_research",
        summary_zh="咨询/投研风格报告：强调 executive summary、结构化洞察、判断、风险和下一步建议。",
        summary_en="Consulting/analyst-style report focused on executive summary, structured insights, judgement, risks, and next steps.",
        pipeline_steps=["research-scope", "analyst-framework", "insight-synthesis", "recommendations"],
        best_for=["market", "industry", "company", "investment", "general"],
        expected_artifacts=["candidate-report.md", "candidate-report.html", "sources.json", "evidence-table.md", "claim-checks.md", "limitations.md", "summary.md"],
        qa_checks=["executive_summary_clear", "insights_actionable", "risks_explicit", "source_quality_noted"],
    ),
    "evidence-table-report": ResearchPipeline(
        id="evidence-table-report",
        name="evidence-table research report",
        role="evidence_table_research",
        summary_zh="先建立 claim-evidence 表，再生成报告，适合严肃调研和需要审计证据链的任务。",
        summary_en="Builds a claim-evidence table before the report, suitable for rigorous research and auditable evidence chains.",
        pipeline_steps=["claim-map", "evidence-table", "claim-checks", "report-synthesis"],
        best_for=["market", "industry", "company", "literature", "investment", "general"],
        expected_artifacts=["candidate-report.md", "candidate-report.html", "sources.json", "evidence-table.md", "claim-checks.md", "limitations.md", "summary.md"],
        qa_checks=["evidence_table_complete", "unsupported_claims_marked", "confidence_per_claim", "limitations_explicit"],
    ),
    "company-research-report": ResearchPipeline(
        id="company-research-report",
        name="company-research + report package",
        role="external_company_research",
        summary_zh="使用真实 company-research skill 做公司、竞品、产品策略和组织背景调研，再输出可比较报告包。",
        summary_en="Uses a real company-research skill for company, competitor, product strategy, and org-context research.",
        pipeline_steps=[COMPANY_RESEARCH_SOURCE, "report-package", "source-and-claim-qa"],
        best_for=["company", "competitive", "market"],
        expected_artifacts=["candidate-report.md", "candidate-report.html", "sources.json", "evidence-table.md", "claim-checks.md", "limitations.md", "summary.md"],
        qa_checks=["executive_quotes_sourced", "strategy_claims_supported", "competitive_context_clear", "limitations_explicit"],
        skill_source=COMPANY_RESEARCH_SOURCE,
    ),
    "user-research-cookiy-report": ResearchPipeline(
        id="user-research-cookiy-report",
        name="user-research-cookiy + report package",
        role="external_user_research",
        summary_zh="使用真实 user-research-cookiy skill 做用户研究计划、访谈/问卷设计或访谈资料综合报告。",
        summary_en="Uses the real user-research-cookiy skill for study plans, interview/survey design, or transcript synthesis reports.",
        pipeline_steps=[USER_RESEARCH_SOURCE, "research-synthesis-package", "source-and-claim-qa"],
        best_for=["user", "customer", "interview", "survey"],
        expected_artifacts=["candidate-report.md", "candidate-report.html", "sources.json", "evidence-table.md", "claim-checks.md", "limitations.md", "summary.md"],
        qa_checks=["research_goal_clear", "method_fit_explained", "participant_or_transcript_limits_marked", "findings_evidence_backed"],
        skill_source=USER_RESEARCH_SOURCE,
    ),
    "literature-review-report": ResearchPipeline(
        id="literature-review-report",
        name="literature-review + report package",
        role="external_literature_review",
        summary_zh="使用真实 literature-review skill 做学术/技术文献调研，输出结构化综述报告和证据表。",
        summary_en="Uses a real literature-review skill for academic or technical literature reviews with an evidence table.",
        pipeline_steps=[LITERATURE_REVIEW_SOURCE, "literature-synthesis-package", "source-and-claim-qa"],
        best_for=["literature", "academic", "technical"],
        expected_artifacts=["candidate-report.md", "candidate-report.html", "sources.json", "evidence-table.md", "claim-checks.md", "limitations.md", "summary.md"],
        qa_checks=["papers_cited", "methods_compared", "recency_risk_marked", "limitations_explicit"],
        skill_source=LITERATURE_REVIEW_SOURCE,
    ),
    "investment-research-report": ResearchPipeline(
        id="investment-research-report",
        name="investment-research + report package",
        role="external_investment_research",
        summary_zh="使用真实 investment-research skill 做投研/行业机会分析，并明确风险、假设和非投资建议边界。",
        summary_en="Uses a real investment-research skill for investment or sector opportunity research with risk and assumption boundaries.",
        pipeline_steps=[INVESTMENT_RESEARCH_SOURCE, "investment-report-package", "risk-qa"],
        best_for=["investment", "finance", "industry"],
        expected_artifacts=["candidate-report.md", "candidate-report.html", "sources.json", "evidence-table.md", "claim-checks.md", "limitations.md", "summary.md"],
        qa_checks=["financial_claims_sourced", "risks_explicit", "assumptions_marked", "not_investment_advice"],
        skill_source=INVESTMENT_RESEARCH_SOURCE,
    ),
}


def _compact(text: str) -> str:
    return "".join(text.lower().split())


def detect_research_type(task_text: str) -> str:
    """Classify the research request into a coarse report family."""
    compact = _compact(task_text)
    if any(word in compact for word in ["用户研究", "用户调研", "访谈", "问卷", "userresearch", "interview", "survey", "persona", "churn", "用户痛点"]):
        return "user"
    if any(word in compact for word in ["文献", "综述", "literaturereview", "paperreview", "academicreview", "技术调研", "方法调研"]):
        return "literature"
    if any(word in compact for word in ["投研", "投资", "股票", "财报", "investment", "equity", "valuation", "financial"]):
        return "investment"
    if any(word in compact for word in ["公司调研", "竞品", "竞对", "competitive", "competitor", "companyresearch", "公司研究"]):
        return "company"
    if any(word in compact for word in ["行业", "市场", "market", "industry", "tam", "sam", "som"]):
        return "market"
    return "general"


def default_pipeline_ids(research_type: str, max_candidates: int = 4) -> list[str]:
    if research_type == "user":
        ordered = ["baseline-research-report", "user-research-cookiy-report", "evidence-table-report", "source-first-research"]
    elif research_type == "company":
        ordered = ["baseline-research-report", "company-research-report", "source-first-research", "analyst-style-report"]
    elif research_type == "literature":
        ordered = ["baseline-research-report", "literature-review-report", "source-first-research", "evidence-table-report"]
    elif research_type == "investment":
        ordered = ["baseline-research-report", "investment-research-report", "analyst-style-report", "source-first-research"]
    else:
        ordered = ["baseline-research-report", "source-first-research", "analyst-style-report", "evidence-table-report"]
    return ordered[:max_candidates]


def _slugify(value: str, default: str = "research-run") -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip().lower()).strip("-")
    return cleaned or default


def split_skill_source(source: str) -> tuple[str, str | None]:
    if "#" not in source:
        return source, None
    base, _, fragment = source.partition("#")
    subdir = fragment.strip().strip("/")
    return base, subdir or None


def _label_from_skill_source(source: str) -> str:
    base, subdir = split_skill_source(source)
    if subdir:
        return subdir.rstrip("/").split("/")[-1] or "external-research-skill"
    if base.startswith(("http://", "https://")):
        return base.rstrip("/").split("/")[-1].replace(".git", "") or "external-research-skill"
    return Path(base).expanduser().name or "external-research-skill"


def pipeline_from_skill_source(source: str, existing_ids: set[str] | None = None) -> ResearchPipeline:
    label = _label_from_skill_source(source)
    base_id = f"skill-{_slugify(label, 'external-research-skill')}"
    existing_ids = existing_ids or set()
    pipeline_id = base_id
    suffix = 2
    while pipeline_id in existing_ids:
        pipeline_id = f"{base_id}-{suffix}"
        suffix += 1
    return ResearchPipeline(
        id=pipeline_id,
        name=f"{label} + research report package",
        role="external_skill_research_report",
        summary_zh=f"使用外部调研 skill `{label}` 生成可比较的 research report package。",
        summary_en=f"Uses the external research skill `{label}` to generate a comparable research report package.",
        pipeline_steps=[source, "report-package", "source-and-claim-qa"],
        best_for=["market", "industry", "company", "user", "literature", "investment", "general"],
        expected_artifacts=["candidate-report.md", "candidate-report.html", "sources.json", "evidence-table.md", "claim-checks.md", "limitations.md", "summary.md"],
        qa_checks=["source_backed_claims", "clear_structure", "evidence_table_present", "limitations_explicit"],
        skill_source=source,
    )


def build_pipeline_registry(skill_sources: list[str] | None = None) -> tuple[dict[str, ResearchPipeline], list[str]]:
    pipelines = dict(RESEARCH_PIPELINES)
    dynamic_ids: list[str] = []
    for source in skill_sources or []:
        pipeline = pipeline_from_skill_source(source, existing_ids=set(pipelines))
        pipelines[pipeline.id] = pipeline
        dynamic_ids.append(pipeline.id)
    return pipelines, dynamic_ids


def _relative(path: Path, base: Path) -> str:
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


def _kind_for(path: Path) -> str:
    suffix = path.suffix.lstrip(".")
    if not suffix:
        return "file"
    return suffix.upper()


def _read_optional(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()
    except UnicodeDecodeError:
        return ""


def load_pipeline_skill_prompt(pipeline: ResearchPipeline) -> str:
    if not pipeline.skill_source:
        return ""
    enabled = os.environ.get("FORKPROBE_RESEARCH_LOAD_SKILL_PROMPTS", "1").lower()
    if enabled in FALSE_VALUES:
        return ""
    try:
        sys.path.insert(0, str(SCRIPT_DIR))
        from skill_loader import load_skill

        source, subdir = split_skill_source(pipeline.skill_source)
        loaded = load_skill(skill_id=pipeline.id, source=source, subdir=subdir)
        return loaded.to_system_prompt()
    except Exception as exc:
        return (
            f"Could not load skill instructions from {pipeline.skill_source}: "
            f"{type(exc).__name__}: {exc}\n"
            "Continue with this pipeline's built-in research instructions and clearly note this fallback in summary.md."
        )


def _load_run_result(candidate_dir: Path) -> dict[str, Any]:
    path = candidate_dir / "run-result.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _preview_for(path: Path, artifact_dir: Path, output_dir: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in PREVIEW_SUFFIXES:
        return _relative(path, output_dir)
    report_html = artifact_dir / "candidate-report.html"
    if report_html.exists():
        return _relative(report_html, output_dir)
    return ""


def collect_candidate_artifacts(candidate_dir: Path, output_dir: Path) -> list[dict[str, Any]]:
    artifact_dir = candidate_dir / "artifacts"
    if not artifact_dir.exists():
        return []
    artifacts: list[dict[str, Any]] = []
    for path in sorted(p for p in artifact_dir.rglob("*") if p.is_file()):
        if path.name == ".gitkeep" or path.suffix.lower() not in ARTIFACT_SUFFIXES:
            continue
        entry = {
            "path": _relative(path, output_dir),
            "label": _relative(path, artifact_dir),
            "kind": _kind_for(path),
        }
        preview = _preview_for(path, artifact_dir, output_dir)
        if preview:
            entry["preview_path"] = preview
        artifacts.append(entry)
    return artifacts


def _has_generated_artifacts(candidate_dir: Path) -> bool:
    artifact_dir = candidate_dir / "artifacts"
    if not artifact_dir.exists():
        return False
    return any(path.is_file() and path.name != ".gitkeep" for path in artifact_dir.rglob("*"))


def candidate_summary(pipeline: ResearchPipeline, candidate_dir: Path) -> str:
    parts = [pipeline.summary_zh]
    for filename, title in (
        ("summary.md", "Summary"),
        ("runner-output.md", "Runner output"),
        ("candidate-report.md", "Report preview"),
        ("evidence-table.md", "Evidence table"),
        ("claim-checks.md", "Claim checks"),
        ("limitations.md", "Limitations"),
    ):
        text = _read_optional(candidate_dir / filename) or _read_optional(candidate_dir / "artifacts" / filename)
        if text:
            parts.append(f"\n\n## {title}\n{text}")
    return "\n".join(parts)


def estimate_candidate_tokens(
    task_input: str,
    pipeline: ResearchPipeline,
    candidate_dir: Path,
    summary: str,
    artifacts: list[dict[str, Any]],
    run_result: dict[str, Any],
) -> int:
    sys.path.insert(0, str(SCRIPT_DIR))
    from compare import estimate_text_tokens

    prompt = _read_optional(candidate_dir / "RUN_PROMPT.md") or _read_optional(candidate_dir / "INSTRUCTIONS.md")
    if not prompt:
        prompt = "\n\n".join([
            pipeline.summary_zh,
            " -> ".join(pipeline.pipeline_steps),
            task_input,
        ])
    artifact_text = "\n".join(
        f"{artifact.get('label') or artifact.get('path') or ''} "
        f"{artifact.get('kind') or ''} "
        f"{artifact.get('path') or ''}"
        for artifact in artifacts
    )
    visible_text = "\n\n".join(
        part for part in [
            prompt,
            str(run_result.get("output") or ""),
            summary,
            artifact_text,
        ]
        if part
    )
    return estimate_text_tokens(visible_text)


def build_pipeline_instructions(task_input: str, pipeline: ResearchPipeline, candidate_dir: Path) -> str:
    artifact_dir = candidate_dir / "artifacts"
    expected = "\n".join(f"- `{name}`" for name in pipeline.expected_artifacts)
    qa = "\n".join(f"- {check}" for check in pipeline.qa_checks)
    steps = " -> ".join(pipeline.pipeline_steps)
    skill_source = f"\nExternal skill source: `{pipeline.skill_source}`\n" if pipeline.skill_source else ""
    return f"""# {pipeline.name}

## Goal

Generate a research report artifact package for the same original task as every other forkprobe candidate.

## Original Task

{task_input}

## Pipeline

{steps}
{skill_source}

## Output Directory

Write all candidate outputs under:

`{artifact_dir}`

## Expected Research Package

{expected}

Required artifact details:
- `candidate-report.md`: final deliverable report with executive summary, sections, findings, recommendations or next steps.
- `candidate-report.html`: lightweight HTML preview of the same report when practical.
- `sources.json`: array of sources with title, url, publisher, date, accessed_date, relevance, reliability, and which claims each source supports.
- `evidence-table.md`: table with Claim | Evidence | Source | Confidence | Notes.
- `claim-checks.md`: list unsupported, weakly supported, inferred, stale, or high-risk claims.
- `limitations.md`: scope limits, missing data, stale-risk, methodological caveats, and user follow-up needed.
- `summary.md`: short candidate summary for the comparison report.

Source and evidence rules:
- Prefer primary or authoritative sources when available.
- Do not invent URLs, titles, authors, dates, metrics, or quotes.
- Mark inference clearly when a claim is not directly source-backed.
- For high-stakes finance, legal, medical, or policy topics, include a strong limitation note and avoid giving final professional advice.
- If you cannot browse or verify a source, say so in `limitations.md` and mark affected claims in `claim-checks.md`.

## QA Checks

{qa}

## Candidate Summary

After generating artifacts, write `summary.md` in this candidate directory. Include:

- what report angle this pipeline used
- which files were generated
- source quality and evidence-chain strengths
- known limitations or manual cleanup needed
"""


def build_manifest(
    task_input: str,
    output_dir: Path,
    pipeline_ids: list[str],
    research_type: str,
    pipeline_registry: dict[str, ResearchPipeline] | None = None,
) -> dict[str, Any]:
    pipeline_registry = pipeline_registry or RESEARCH_PIPELINES
    candidates = []
    for pipeline_id in pipeline_ids:
        pipeline = pipeline_registry[pipeline_id]
        candidate_dir = output_dir / "candidates" / pipeline.id
        run_result = _load_run_result(candidate_dir)
        summary = candidate_summary(pipeline, candidate_dir)
        artifacts = collect_candidate_artifacts(candidate_dir, output_dir)
        provider_tokens = int(run_result.get("tokens_used") or 0)
        estimated_tokens = estimate_candidate_tokens(
            task_input=task_input,
            pipeline=pipeline,
            candidate_dir=candidate_dir,
            summary=summary,
            artifacts=artifacts,
            run_result=run_result,
        )
        candidates.append({
            "id": pipeline.id,
            "name": pipeline.name,
            "category": "research-artifact",
            "summary": summary,
            "workdir": _relative(candidate_dir, output_dir),
            "pipeline_steps": list(pipeline.pipeline_steps),
            "skill_source": pipeline.skill_source,
            "expected_artifacts": list(pipeline.expected_artifacts),
            "qa_checks": list(pipeline.qa_checks),
            "artifacts": artifacts,
            "tokens_used": provider_tokens,
            "provider_tokens_used": provider_tokens,
            "estimated_tokens_used": estimated_tokens,
            "latency_seconds": float(run_result.get("latency_seconds") or 0.0),
            "error": run_result.get("error"),
        })

    return {
        "schema_version": "research-artifact-v0.1",
        "deliverable_type": "research_report",
        "research_type": research_type,
        "task_input_path": "task.md",
        "duration_seconds": 0,
        "artifact_contract": {
            "required_report": "candidate-report.md or candidate-report.html",
            "recommended_sources": ["sources.json", "evidence-table.md"],
            "recommended_notes": ["claim-checks.md", "limitations.md", "summary.md"],
        },
        "candidates": candidates,
    }


def build_artifact_judge_results(manifest: dict[str, Any]) -> list[Any]:
    sys.path.insert(0, str(SCRIPT_DIR))
    from compare import RunResult

    results = []
    for candidate in manifest.get("candidates", []):
        artifacts = candidate.get("artifacts", [])
        runner_error = candidate.get("error")
        artifact_lines = []
        for artifact in artifacts:
            label = artifact.get("label") or artifact.get("path") or "artifact"
            kind = artifact.get("kind") or "file"
            preview = artifact.get("preview_path") or artifact.get("preview_href") or ""
            artifact_lines.append(f"- {label} ({kind})" + (f", preview: {preview}" if preview else ""))
        artifact_section = "\n".join(artifact_lines) if artifact_lines else "No generated artifacts found."
        output = (
            f"{candidate.get('summary') or ''}\n\n"
            f"## Generated artifacts\n{artifact_section}\n\n"
            f"## Expected artifacts\n" + "\n".join(f"- {item}" for item in candidate.get("expected_artifacts", [])) + "\n\n"
            f"## QA checks\n" + "\n".join(f"- {item}" for item in candidate.get("qa_checks", []))
        )
        if runner_error:
            output += f"\n\n## Runner issue\n{runner_error}"
        results.append(RunResult(
            skill_id=str(candidate.get("id") or candidate.get("name") or "candidate"),
            skill_name=str(candidate.get("name") or candidate.get("id") or "candidate"),
            skill_author=str(candidate.get("author") or ""),
            skill_category=str(candidate.get("category") or "research-artifact"),
            output=output,
            tokens_used=int(candidate.get("tokens_used") or 0),
            latency_seconds=float(candidate.get("latency_seconds") or 0.0),
            estimated_tokens_used=int(candidate.get("estimated_tokens_used") or 0),
            provider_tokens_used=int(candidate.get("provider_tokens_used") or candidate.get("tokens_used") or 0),
            error=None if artifacts else runner_error,
        ))
    return results


def run_artifact_judge(
    task_input: str,
    manifest: dict[str, Any],
    rubric: str | None = None,
    timeout: int = 120,
) -> dict[str, Any]:
    sys.path.insert(0, str(SCRIPT_DIR))
    from compare import run_judge

    rubric_text = rubric or (
        "Evaluate the generated research report packages. Prefer candidates with useful conclusions, "
        "reliable and relevant sources, explicit evidence chains, clear structure, honest limitations, "
        "and actionable next steps. Penalize unsupported claims, invented citations, stale data risk, "
        "missing sources.json, missing evidence tables, and missing limitations."
    )
    results = build_artifact_judge_results(manifest)
    with contextlib.redirect_stdout(sys.stderr):
        judge = run_judge(task_input=task_input, results=results, rubric=rubric_text, timeout=timeout)
    return asdict(judge)


def create_workspace(
    task_input: str,
    output_dir: Path,
    pipeline_ids: list[str] | None = None,
    skill_sources: list[str] | None = None,
    max_candidates: int = 4,
) -> dict[str, Any]:
    output_dir = output_dir.expanduser().resolve()
    research_type = detect_research_type(task_input)
    pipeline_registry, dynamic_ids = build_pipeline_registry(skill_sources)
    selected_ids = pipeline_ids or default_pipeline_ids(research_type, max_candidates=max_candidates)
    selected_ids = list(selected_ids)
    for dynamic_id in dynamic_ids:
        if dynamic_id not in selected_ids:
            selected_ids.append(dynamic_id)
    unknown = [pipeline_id for pipeline_id in selected_ids if pipeline_id not in pipeline_registry]
    if unknown:
        raise KeyError(f"Unknown research pipeline(s): {', '.join(unknown)}")

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "task.md").write_text(task_input, encoding="utf-8")

    for pipeline_id in selected_ids:
        pipeline = pipeline_registry[pipeline_id]
        candidate_dir = output_dir / "candidates" / pipeline.id
        artifact_dir = candidate_dir / "artifacts"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        (artifact_dir / ".gitkeep").write_text("", encoding="utf-8")
        (candidate_dir / "INSTRUCTIONS.md").write_text(
            build_pipeline_instructions(task_input, pipeline, candidate_dir),
            encoding="utf-8",
        )

    manifest = build_manifest(task_input, output_dir, selected_ids, research_type, pipeline_registry=pipeline_registry)
    manifest_path = output_dir / "artifact-manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "output_dir": str(output_dir),
        "manifest_path": str(manifest_path),
        "research_type": research_type,
        "pipelines": selected_ids,
        "skill_sources": list(skill_sources or []),
        "manifest": manifest,
    }


def _codex_cli_path() -> str | None:
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


def _parse_codex_tokens(text: str) -> int:
    match = re.search(r"tokens used\s+([0-9][0-9,]*)", text, flags=re.IGNORECASE)
    if not match:
        return 0
    return int(match.group(1).replace(",", ""))


def _tail(text: str, limit: int = 1400) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return "..." + text[-limit:]


def build_candidate_run_prompt(task_input: str, pipeline: ResearchPipeline, candidate_dir: Path) -> str:
    instructions = (candidate_dir / "INSTRUCTIONS.md").read_text(encoding="utf-8")
    artifact_dir = candidate_dir / "artifacts"
    skill_prompt = load_pipeline_skill_prompt(pipeline)
    skill_section = ""
    if skill_prompt:
        skill_section = f"""
## External Skill Instructions

This pipeline is backed by an external research skill. Apply these instructions before producing the report package:

{skill_prompt}
"""
    return f"""You are running one isolated ForkProbe research-report candidate.

Your job is to generate the requested research artifact package, not to compare candidates.

Hard requirements:
- Write all generated files under `{artifact_dir}`.
- Create or update `{candidate_dir / "summary.md"}`.
- Include `candidate-report.md` and preferably `candidate-report.html`.
- Include `sources.json`, `evidence-table.md`, `claim-checks.md`, and `limitations.md` when possible.
- Do not ask the user follow-up questions.
- Do not modify files outside `{candidate_dir}` unless required by the toolchain cache.
- After the files are written, respond with a concise completion summary and stop.

{skill_section}

{instructions}

## Original task, repeated for convenience

{task_input}
"""


def run_candidate_codex(
    task_input: str,
    output_dir: Path,
    pipeline_id: str,
    timeout: int = 900,
    pipeline_registry: dict[str, ResearchPipeline] | None = None,
) -> ResearchRunResult:
    pipeline_registry = pipeline_registry or RESEARCH_PIPELINES
    if pipeline_id not in pipeline_registry:
        raise KeyError(f"Unknown research pipeline: {pipeline_id}")
    pipeline = pipeline_registry[pipeline_id]
    candidate_dir = output_dir / "candidates" / pipeline.id
    candidate_dir.mkdir(parents=True, exist_ok=True)
    (candidate_dir / "artifacts").mkdir(parents=True, exist_ok=True)
    prompt = build_candidate_run_prompt(task_input, pipeline, candidate_dir)
    (candidate_dir / "RUN_PROMPT.md").write_text(prompt, encoding="utf-8")

    cli = _codex_cli_path()
    if not cli:
        result = ResearchRunResult(
            pipeline_id=pipeline.id,
            output="",
            tokens_used=0,
            latency_seconds=0.0,
            error="Codex CLI not found. Set FORKPROBE_CODEX_CLI or install Codex CLI.",
        )
        _write_run_result(candidate_dir, result)
        return result

    sandbox = os.environ.get("FORKPROBE_RESEARCH_SANDBOX", "workspace-write")
    model = os.environ.get("FORKPROBE_MODEL_CODEX_NATIVE")
    reasoning_effort = os.environ.get("FORKPROBE_CODEX_REASONING_EFFORT")
    t0 = time.time()
    output_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(prefix="forkprobe-research-codex-", suffix=".txt", delete=False) as f:
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
            str(PROJECT_DIR),
            "--add-dir",
            str(output_dir),
        ]
        if model:
            cmd.extend(["--model", model])
        if reasoning_effort:
            cmd.extend(["-c", f'model_reasoning_effort="{reasoning_effort}"'])
        cmd.append("-")

        proc = subprocess.run(
            cmd,
            input=prompt,
            text=True,
            capture_output=True,
            timeout=timeout,
        )
        transcript = f"{proc.stdout}\n{proc.stderr}"
        output = output_path.read_text(encoding="utf-8").strip() if output_path and output_path.exists() else ""
        if not output:
            output = proc.stdout.strip()
        error = None
        if proc.returncode != 0:
            error = f"Codex CLI exited {proc.returncode}: {_tail(transcript)}"
        result = ResearchRunResult(
            pipeline_id=pipeline.id,
            output=output,
            tokens_used=_parse_codex_tokens(transcript),
            latency_seconds=time.time() - t0,
            error=error,
        )
        _write_run_result(candidate_dir, result)
        return result
    except subprocess.TimeoutExpired:
        partial_note = " Partial artifacts are available." if _has_generated_artifacts(candidate_dir) else ""
        result = ResearchRunResult(
            pipeline_id=pipeline.id,
            output="",
            tokens_used=0,
            latency_seconds=time.time() - t0,
            error=f"Codex CLI timeout after {timeout}s.{partial_note}",
        )
        _write_run_result(candidate_dir, result)
        return result
    except Exception as exc:
        result = ResearchRunResult(
            pipeline_id=pipeline.id,
            output="",
            tokens_used=0,
            latency_seconds=time.time() - t0,
            error=f"{type(exc).__name__}: {exc}",
        )
        _write_run_result(candidate_dir, result)
        return result
    finally:
        if output_path:
            try:
                output_path.unlink(missing_ok=True)
            except OSError:
                pass


def _write_run_result(candidate_dir: Path, result: ResearchRunResult) -> None:
    payload = {
        "pipeline_id": result.pipeline_id,
        "output": result.output,
        "tokens_used": result.tokens_used,
        "latency_seconds": result.latency_seconds,
        "error": result.error,
    }
    (candidate_dir / "run-result.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if result.output:
        (candidate_dir / "runner-output.md").write_text(result.output, encoding="utf-8")
    if result.error:
        (candidate_dir / "runner-error.txt").write_text(result.error, encoding="utf-8")


def run_parallel(
    task_input: str,
    output_dir: Path,
    pipeline_ids: list[str],
    pipeline_registry: dict[str, ResearchPipeline] | None = None,
    max_workers: int = 2,
    timeout: int = 900,
) -> list[ResearchRunResult]:
    pipeline_registry = pipeline_registry or RESEARCH_PIPELINES
    max_workers = int(os.environ.get("FORKPROBE_RESEARCH_MAX_WORKERS", str(max_workers)))
    results: list[ResearchRunResult] = []
    with ThreadPoolExecutor(max_workers=min(max_workers, len(pipeline_ids))) as executor:
        futures = {
            executor.submit(run_candidate_codex, task_input, output_dir, pipeline_id, timeout, pipeline_registry): pipeline_id
            for pipeline_id in pipeline_ids
        }
        for future in as_completed(futures):
            pipeline_id = futures[future]
            candidate_dir = output_dir / "candidates" / pipeline_id
            try:
                result = future.result()
            except Exception as exc:
                result = ResearchRunResult(
                    pipeline_id=pipeline_id,
                    output="",
                    tokens_used=0,
                    latency_seconds=0.0,
                    error=f"{type(exc).__name__}: {exc}",
                )
                _write_run_result(candidate_dir, result)
            status = "ok" if not result.error else ("partial" if _has_generated_artifacts(candidate_dir) else "error")
            print(f"[forkprobe] research pipeline {pipeline_id}: {status} ({result.latency_seconds:.1f}s)", file=sys.stderr)
            results.append(result)
    order = {pipeline_id: idx for idx, pipeline_id in enumerate(pipeline_ids)}
    results.sort(key=lambda result: order.get(result.pipeline_id, 999))
    return results


def _read_task(args: argparse.Namespace) -> str:
    if args.input:
        return Path(args.input).expanduser().read_text(encoding="utf-8")
    if args.text:
        return args.text
    return sys.stdin.read()


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare a research report artifact comparison workspace")
    parser.add_argument("--input", help="Path to task input text")
    parser.add_argument("--text", help="Task description text. Used when --input is omitted")
    parser.add_argument("--output-dir", help="Workspace directory. Defaults to outputs/research-runs/<timestamp>")
    parser.add_argument("--pipeline", action="append", default=[], help="Pipeline id to include. Repeat to override defaults")
    parser.add_argument("--skill-source", action="append", default=[], help="External research skill source to run as a BYO pipeline. Repeat for multiple skills")
    parser.add_argument("--max-candidates", type=int, default=4, help="Maximum default pipelines when --pipeline is omitted")
    parser.add_argument("--run", action="store_true", help="Run selected pipelines in parallel with Codex native CLI")
    parser.add_argument("--confirmed", action="store_true", help="Acknowledge the user has confirmed the research pipeline shortlist before --run")
    parser.add_argument("--timeout", type=int, default=900, help="Seconds to wait for each candidate run (default: 900)")
    parser.add_argument("--max-workers", type=int, default=2, help="Maximum concurrent candidate runs for --run")
    parser.add_argument("--render-report", action="store_true", help="Render an initial artifact report from the manifest")
    parser.add_argument("--report-output", default="research-artifact-report.html", help="Report HTML path when --render-report is set")
    parser.add_argument("--judge", action="store_true", help="Run an AI judge over generated research artifact summaries")
    parser.add_argument("--judge-rubric", default=None, help="Optional extra rubric text for --judge")
    parser.add_argument("--judge-timeout", type=int, default=120, help="Seconds to wait for the judge subagent (default: 120)")
    parser.add_argument("--no-open", action="store_true", help="Do not auto-open rendered report")
    parser.add_argument("--json", action="store_true", help="Print JSON summary")
    args = parser.parse_args()

    task_input = _read_task(args)
    if not task_input.strip():
        raise SystemExit("Task input is empty.")
    if args.run and not args.confirmed:
        raise SystemExit(
            "Refusing to run research pipelines before candidate confirmation. "
            "First run `python3 scripts/recommend.py --input <input.txt>`, show the shortlist to the user, "
            "then rerun research_artifact.py with --confirmed after the user confirms."
        )

    timestamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    default_name = f"{timestamp}-{_slugify(task_input[:48])}"
    output_dir = Path(args.output_dir).expanduser() if args.output_dir else DEFAULT_OUTPUT_ROOT / default_name
    result = create_workspace(
        task_input=task_input,
        output_dir=output_dir,
        pipeline_ids=args.pipeline or None,
        skill_sources=args.skill_source,
        max_candidates=args.max_candidates,
    )
    pipeline_registry, _dynamic_ids = build_pipeline_registry(args.skill_source)

    if args.run:
        run_parallel(
            task_input=task_input,
            output_dir=Path(result["output_dir"]),
            pipeline_ids=list(result["pipelines"]),
            pipeline_registry=pipeline_registry,
            max_workers=args.max_workers,
            timeout=args.timeout,
        )
        result = create_workspace(
            task_input=task_input,
            output_dir=Path(result["output_dir"]),
            pipeline_ids=list(result["pipelines"]),
            skill_sources=args.skill_source,
            max_candidates=args.max_candidates,
        )

    if args.judge:
        manifest_path = Path(result["manifest_path"])
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["judge"] = run_artifact_judge(
            task_input=task_input,
            manifest=manifest,
            rubric=args.judge_rubric,
            timeout=args.judge_timeout,
        )
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        result["manifest"] = manifest

    if args.render_report or args.run:
        sys.path.insert(0, str(SCRIPT_DIR))
        from render_artifact_report import render_from_manifest

        report_path = Path(args.report_output)
        if not report_path.is_absolute():
            report_path = Path(result["output_dir"]) / report_path
        render_from_manifest(
            manifest_path=Path(result["manifest_path"]),
            output_path=report_path,
            auto_open=not args.no_open,
        )
        result["report_path"] = str(report_path.resolve())

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"[forkprobe] Research workspace: {result['output_dir']}")
        print(f"[forkprobe] Research type: {result['research_type']}")
        print(f"[forkprobe] Pipelines: {', '.join(result['pipelines'])}")
        print(f"[forkprobe] Manifest: {result['manifest_path']}")
        if result.get("report_path"):
            print(f"[forkprobe] Report: {result['report_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
