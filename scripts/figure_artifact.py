"""
Prepare a scientific figure artifact comparison run.

This is the first framework layer for visual artifact mode. It does not try to
generate figures by itself. Instead, it creates one isolated workspace per
pipeline, writes clear candidate instructions, and produces an artifact manifest
that can be filled with PNG/SVG/PDF/TIFF/code outputs and rendered by
render_artifact_report.py.
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
import time
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).parent
PROJECT_DIR = SCRIPT_DIR.parent
DEFAULT_OUTPUT_ROOT = PROJECT_DIR / "outputs" / "figure-runs"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"}
FALSE_VALUES = {"0", "false", "no", "off"}
ARTIFACT_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".pdf", ".tif", ".tiff",
    ".py", ".r", ".R", ".jl", ".ipynb", ".drawio", ".json", ".md", ".txt",
}


@dataclass(frozen=True)
class FigurePipeline:
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
class FigureRunResult:
    pipeline_id: str
    output: str
    tokens_used: int
    latency_seconds: float
    error: str | None = None


FIGURE_PIPELINES: dict[str, FigurePipeline] = {
    "baseline-python-figure": FigurePipeline(
        id="baseline-python-figure",
        name="baseline + Python figure package",
        role="baseline_renderer",
        summary_zh="不使用专门科研作图 skill，直接生成可复现的 Python/SVG 图包，作为成品基线。",
        summary_en="No specialized figure skill; produces a reproducible Python/SVG figure package as the baseline.",
        pipeline_steps=["baseline", "python/matplotlib-or-svg", "artifact-qa"],
        best_for=["plot", "schematic", "mixed"],
        expected_artifacts=["preview.png", "figure.svg", "figure.pdf", "source.py or source.svg", "caption.md", "qa.md"],
        qa_checks=["readable_labels", "no_overlap", "export_files_open", "caption_matches_visual"],
    ),
    "nature-figure-python": FigurePipeline(
        id="nature-figure-python",
        name="nature-figure + Python/SVG renderer",
        role="scientific_design_then_render",
        summary_zh="先用 nature-figure 做科学设计、storyline、panel 结构和图注，再生成可投稿图包。",
        summary_en="Uses nature-figure for scientific design, storyline, panel structure, and caption before rendering a submission-oriented package.",
        pipeline_steps=["nature-figure", "python/svg-renderer", "artifact-qa"],
        best_for=["plot", "schematic", "graphical_abstract", "mixed"],
        expected_artifacts=["preview.png", "figure.svg", "figure.pdf", "figure.tiff", "source.py or source.svg", "caption.md", "qa.md"],
        qa_checks=["scientific_fidelity", "panel_logic", "journal_style", "export_files_open", "caption_matches_visual"],
        skill_source="https://github.com/Yuan1z0825/nature-skills#skills/nature-figure",
    ),
    "plot-code-python": FigurePipeline(
        id="plot-code-python",
        name="data plot code pipeline",
        role="plot_pipeline",
        summary_zh="面向真实数据作图：读取数据、生成绘图代码、导出 PNG/SVG/PDF/TIFF 和简短图注。",
        summary_en="For real data plots: load data, generate plotting code, export PNG/SVG/PDF/TIFF, and write a short caption.",
        pipeline_steps=["data-understanding", "python/matplotlib-or-seaborn", "export", "artifact-qa"],
        best_for=["plot"],
        expected_artifacts=["preview.png", "figure.svg", "figure.pdf", "figure.tiff", "source.py", "caption.md", "qa.md"],
        qa_checks=["data_mapping_correct", "axis_units_clear", "legend_readable", "export_files_open", "reproducible_code"],
    ),
    "schematic-svg": FigurePipeline(
        id="schematic-svg",
        name="schematic SVG / draw.io pipeline",
        role="schematic_pipeline",
        summary_zh="面向机制图、架构图和流程图：先设计布局，再生成 SVG/draw.io 友好的矢量图包。",
        summary_en="For mechanism, architecture, and workflow diagrams: design layout first, then produce an SVG/draw.io-friendly vector package.",
        pipeline_steps=["brief-to-layout", "svg-or-drawio", "export", "artifact-qa"],
        best_for=["schematic", "graphical_abstract", "mixed"],
        expected_artifacts=["preview.png", "figure.svg", "figure.drawio or layout.json", "caption.md", "qa.md"],
        qa_checks=["layout_hierarchy_clear", "no_text_overlap", "arrows_unambiguous", "export_files_open", "caption_matches_visual"],
    ),
    "graphical-abstract-svg": FigurePipeline(
        id="graphical-abstract-svg",
        name="graphical abstract SVG pipeline",
        role="graphical_abstract_pipeline",
        summary_zh="面向 graphical abstract：把论文 brief 转成单幅摘要图、导出预览和矢量源文件。",
        summary_en="For graphical abstracts: turn a paper brief into a single visual abstract with preview and vector source files.",
        pipeline_steps=["paper-brief", "visual-storyboard", "svg-render", "artifact-qa"],
        best_for=["graphical_abstract", "schematic", "mixed"],
        expected_artifacts=["preview.png", "figure.svg", "figure.pdf", "caption.md", "qa.md"],
        qa_checks=["main_claim_visible", "visual_flow_clear", "journal_safe_style", "export_files_open"],
    ),
}


def _compact(text: str) -> str:
    return "".join(text.lower().split())


def detect_figure_type(task_text: str) -> str:
    """Classify the scientific figure request into a coarse pipeline family."""
    compact = _compact(task_text)
    if any(word in compact for word in ["graphicalabstract", "图文摘要", "视觉摘要"]):
        return "graphical_abstract"
    if any(word in compact for word in ["csv", "excel", "数据", "data", "plot", "曲线", "柱状图", "散点", "箱线", "热图"]):
        return "plot"
    if any(word in compact for word in ["机制图", "架构图", "示意图", "流程图", "schematic", "diagram", "architecture", "workflow"]):
        return "schematic"
    return "mixed"


def default_pipeline_ids(figure_type: str, max_candidates: int = 4) -> list[str]:
    """Pick a compact default shortlist for the detected figure type."""
    if figure_type == "plot":
        ordered = ["baseline-python-figure", "plot-code-python", "nature-figure-python", "schematic-svg"]
    elif figure_type == "schematic":
        ordered = ["baseline-python-figure", "schematic-svg", "nature-figure-python", "graphical-abstract-svg"]
    elif figure_type == "graphical_abstract":
        ordered = ["baseline-python-figure", "graphical-abstract-svg", "nature-figure-python", "schematic-svg"]
    else:
        ordered = ["baseline-python-figure", "nature-figure-python", "plot-code-python", "schematic-svg"]
    return ordered[:max_candidates]


def _slugify(value: str, default: str = "figure-run") -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip().lower()).strip("-")
    return cleaned or default


def _label_from_skill_source(source: str) -> str:
    base, subdir = split_skill_source(source)
    if subdir:
        return subdir.rstrip("/").split("/")[-1] or "external-figure-skill"
    if base.startswith(("http://", "https://")):
        return base.rstrip("/").split("/")[-1].replace(".git", "") or "external-figure-skill"
    return Path(base).expanduser().name or "external-figure-skill"


def pipeline_from_skill_source(source: str, existing_ids: set[str] | None = None) -> FigurePipeline:
    """Create a generic figure artifact pipeline from a BYO skill source."""
    label = _label_from_skill_source(source)
    base_id = f"skill-{_slugify(label, 'external-figure-skill')}"
    existing_ids = existing_ids or set()
    pipeline_id = base_id
    suffix = 2
    while pipeline_id in existing_ids:
        pipeline_id = f"{base_id}-{suffix}"
        suffix += 1
    return FigurePipeline(
        id=pipeline_id,
        name=f"{label} + Python/SVG renderer",
        role="external_skill_then_render",
        summary_zh=f"使用外部科研作图 skill `{label}` 先做图的科学设计，再生成可比较的 figure package。",
        summary_en=f"Uses the external scientific-figure skill `{label}` for figure design, then renders a comparable figure package.",
        pipeline_steps=[source, "python/svg-renderer", "artifact-qa"],
        best_for=["plot", "schematic", "graphical_abstract", "mixed"],
        expected_artifacts=["preview.png", "figure.svg", "figure.pdf", "source.py or source.svg", "caption.md", "qa.md"],
        qa_checks=["scientific_fidelity", "layout_readable", "no_overlap", "export_files_open", "caption_matches_visual"],
        skill_source=source,
    )


def build_pipeline_registry(skill_sources: list[str] | None = None) -> tuple[dict[str, FigurePipeline], list[str]]:
    """Return built-in plus BYO figure pipelines, preserving deterministic ids."""
    pipelines = dict(FIGURE_PIPELINES)
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


def split_skill_source(source: str) -> tuple[str, str | None]:
    """Split '<repo-or-path>#<subdir>' references used for multi-skill repos."""
    if "#" not in source:
        return source, None
    base, _, fragment = source.partition("#")
    subdir = fragment.strip().strip("/")
    return base, subdir or None


def load_pipeline_skill_prompt(pipeline: FigurePipeline) -> str:
    """Load the concrete skill prompt for pipelines backed by an external skill."""
    if not pipeline.skill_source:
        return ""
    enabled = os.environ.get("FORKPROBE_FIGURE_LOAD_SKILL_PROMPTS", "1").lower()
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
            "Continue with this pipeline's built-in instructions and clearly note this fallback in summary.md."
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
    if suffix in IMAGE_SUFFIXES:
        return _relative(path, output_dir)
    for preview_name in ("preview.png", "preview.jpg", "preview.webp", "figure.png"):
        preview = artifact_dir / preview_name
        if preview.exists():
            return _relative(preview, output_dir)
    return ""


def collect_candidate_artifacts(candidate_dir: Path, output_dir: Path) -> list[dict[str, Any]]:
    """Collect files generated by one figure candidate into report manifest entries."""
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


def candidate_summary(pipeline: FigurePipeline, candidate_dir: Path) -> str:
    """Build the text shown in the artifact report for one candidate."""
    parts = [pipeline.summary_zh]
    for filename, title in (
        ("summary.md", "Summary"),
        ("runner-output.md", "Runner output"),
        ("caption.md", "Caption"),
        ("qa.md", "QA"),
    ):
        text = _read_optional(candidate_dir / filename) or _read_optional(candidate_dir / "artifacts" / filename)
        if text:
            parts.append(f"\n\n## {title}\n{text}")
    return "\n".join(parts)


def estimate_candidate_tokens(
    task_input: str,
    pipeline: FigurePipeline,
    candidate_dir: Path,
    summary: str,
    artifacts: list[dict[str, Any]],
    run_result: dict[str, Any],
) -> int:
    """Estimate visible context for artifact-mode candidates.

    Codex native CLI does not always expose provider token usage, especially
    when a long artifact run times out after writing partial files. The report
    should still show a stable approximate comparison based on prompt text,
    candidate notes, runner output, and generated file metadata.
    """
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


def build_pipeline_instructions(task_input: str, pipeline: FigurePipeline, candidate_dir: Path) -> str:
    artifact_dir = candidate_dir / "artifacts"
    expected = "\n".join(f"- `{name}`" for name in pipeline.expected_artifacts)
    qa = "\n".join(f"- {check}" for check in pipeline.qa_checks)
    steps = " -> ".join(pipeline.pipeline_steps)
    skill_source = f"\nExternal skill source: `{pipeline.skill_source}`\n" if pipeline.skill_source else ""
    return f"""# {pipeline.name}

## Goal

Generate a scientific figure artifact package for the same original task as every other forkprobe candidate.

## Original Task

{task_input}

## Pipeline

{steps}
{skill_source}

## Output Directory

Write all candidate outputs under:

`{artifact_dir}`

## Expected Artifact Package

{expected}

Use filenames that keep the candidate id visible when practical. Prefer editable/reproducible source files alongside exported preview files.

## QA Checks

{qa}

## Candidate Summary

After generating artifacts, write `summary.md` in this candidate directory. Include:

- what the figure shows
- which source files were generated
- what worked well
- known limitations or manual cleanup needed
"""


def build_manifest(
    task_input: str,
    output_dir: Path,
    pipeline_ids: list[str],
    figure_type: str,
    pipeline_registry: dict[str, FigurePipeline] | None = None,
) -> dict[str, Any]:
    pipeline_registry = pipeline_registry or FIGURE_PIPELINES
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
            "category": "figure-artifact",
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
        "schema_version": "figure-artifact-v0.2",
        "deliverable_type": "visual_artifact",
        "figure_type": figure_type,
        "task_input_path": "task.md",
        "duration_seconds": 0,
        "artifact_contract": {
            "required_preview": "PNG preview for report display",
            "recommended_exports": ["SVG", "PDF", "TIFF"],
            "recommended_sources": ["Python, SVG, draw.io, or layout JSON"],
            "recommended_notes": ["caption.md", "qa.md"],
        },
        "candidates": candidates,
    }


def build_artifact_judge_results(manifest: dict[str, Any]) -> list[Any]:
    """Convert artifact manifest candidates into compare.py RunResult rows."""
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
            skill_category=str(candidate.get("category") or "figure-artifact"),
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
    """Run the standard forkprobe judge over figure artifact summaries and file manifests."""
    sys.path.insert(0, str(SCRIPT_DIR))
    from compare import run_judge

    rubric_text = rubric or (
        "Evaluate the generated scientific figure artifact packages. Prefer candidates with "
        "scientifically faithful visual logic, readable layout, clear labels/arrows, useful editable/source files, "
        "complete expected exports, accurate captions, and honest QA notes. Penalize missing previews or missing exports."
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
    figure_type = detect_figure_type(task_input)
    pipeline_registry, dynamic_ids = build_pipeline_registry(skill_sources)
    selected_ids = pipeline_ids or default_pipeline_ids(figure_type, max_candidates=max_candidates)
    selected_ids = list(selected_ids)
    for dynamic_id in dynamic_ids:
        if dynamic_id not in selected_ids:
            selected_ids.append(dynamic_id)
    unknown = [pipeline_id for pipeline_id in selected_ids if pipeline_id not in pipeline_registry]
    if unknown:
        raise KeyError(f"Unknown figure pipeline(s): {', '.join(unknown)}")

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

    manifest = build_manifest(task_input, output_dir, selected_ids, figure_type, pipeline_registry=pipeline_registry)
    manifest_path = output_dir / "artifact-manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "output_dir": str(output_dir),
        "manifest_path": str(manifest_path),
        "figure_type": figure_type,
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


def build_candidate_run_prompt(task_input: str, pipeline: FigurePipeline, candidate_dir: Path) -> str:
    instructions = (candidate_dir / "INSTRUCTIONS.md").read_text(encoding="utf-8")
    artifact_dir = candidate_dir / "artifacts"
    skill_prompt = load_pipeline_skill_prompt(pipeline)
    skill_section = ""
    if skill_prompt:
        skill_section = f"""
## External Skill Instructions

This pipeline is backed by an external skill. Apply these instructions before rendering the artifact package:

{skill_prompt}
"""
    return f"""You are running one isolated ForkProbe scientific-figure candidate.

Your job is to generate the requested figure artifact package, not to compare candidates.

Hard requirements:
- Write all generated files under `{artifact_dir}`.
- Create or update `{candidate_dir / "summary.md"}`.
- Prefer a report-display preview named `preview.png` when possible.
- Include editable or reproducible source files when possible.
- Include `caption.md` and `qa.md` when possible.
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
    pipeline_registry: dict[str, FigurePipeline] | None = None,
) -> FigureRunResult:
    """Run one figure pipeline in an isolated Codex native session."""
    pipeline_registry = pipeline_registry or FIGURE_PIPELINES
    if pipeline_id not in pipeline_registry:
        raise KeyError(f"Unknown figure pipeline: {pipeline_id}")
    pipeline = pipeline_registry[pipeline_id]
    candidate_dir = output_dir / "candidates" / pipeline.id
    candidate_dir.mkdir(parents=True, exist_ok=True)
    (candidate_dir / "artifacts").mkdir(parents=True, exist_ok=True)
    prompt = build_candidate_run_prompt(task_input, pipeline, candidate_dir)
    (candidate_dir / "RUN_PROMPT.md").write_text(prompt, encoding="utf-8")

    cli = _codex_cli_path()
    if not cli:
        result = FigureRunResult(
            pipeline_id=pipeline.id,
            output="",
            tokens_used=0,
            latency_seconds=0.0,
            error="Codex CLI not found. Set FORKPROBE_CODEX_CLI or install Codex CLI.",
        )
        _write_run_result(candidate_dir, result)
        return result

    sandbox = os.environ.get("FORKPROBE_FIGURE_SANDBOX", "workspace-write")
    model = os.environ.get("FORKPROBE_MODEL_CODEX_NATIVE")
    reasoning_effort = os.environ.get("FORKPROBE_CODEX_REASONING_EFFORT")
    t0 = time.time()
    output_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(prefix="forkprobe-figure-codex-", suffix=".txt", delete=False) as f:
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
        result = FigureRunResult(
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
        result = FigureRunResult(
            pipeline_id=pipeline.id,
            output="",
            tokens_used=0,
            latency_seconds=time.time() - t0,
            error=f"Codex CLI timeout after {timeout}s.{partial_note}",
        )
        _write_run_result(candidate_dir, result)
        return result
    except Exception as exc:
        result = FigureRunResult(
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


def _write_run_result(candidate_dir: Path, result: FigureRunResult) -> None:
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
    pipeline_registry: dict[str, FigurePipeline] | None = None,
    max_workers: int = 2,
    timeout: int = 900,
) -> list[FigureRunResult]:
    """Run selected figure pipelines concurrently."""
    pipeline_registry = pipeline_registry or FIGURE_PIPELINES
    max_workers = int(os.environ.get("FORKPROBE_FIGURE_MAX_WORKERS", str(max_workers)))
    results: list[FigureRunResult] = []
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
                result = FigureRunResult(
                    pipeline_id=pipeline_id,
                    output="",
                    tokens_used=0,
                    latency_seconds=0.0,
                    error=f"{type(exc).__name__}: {exc}",
                )
                _write_run_result(candidate_dir, result)
            status = "ok" if not result.error else ("partial" if _has_generated_artifacts(candidate_dir) else "error")
            print(f"[forkprobe] figure pipeline {pipeline_id}: {status} ({result.latency_seconds:.1f}s)", file=sys.stderr)
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
    parser = argparse.ArgumentParser(description="Prepare a scientific figure artifact comparison workspace")
    parser.add_argument("--input", help="Path to task input text")
    parser.add_argument("--text", help="Task description text. Used when --input is omitted")
    parser.add_argument("--output-dir", help="Workspace directory. Defaults to outputs/figure-runs/<timestamp>")
    parser.add_argument("--pipeline", action="append", default=[], help="Pipeline id to include. Repeat to override defaults")
    parser.add_argument("--skill-source", action="append", default=[], help="External figure skill source to run as a BYO pipeline. Repeat for multiple skills")
    parser.add_argument("--max-candidates", type=int, default=4, help="Maximum default pipelines when --pipeline is omitted")
    parser.add_argument("--run", action="store_true", help="Run selected pipelines in parallel with Codex native CLI")
    parser.add_argument("--timeout", type=int, default=900, help="Seconds to wait for each candidate run (default: 900)")
    parser.add_argument("--max-workers", type=int, default=2, help="Maximum concurrent candidate runs for --run")
    parser.add_argument("--render-report", action="store_true", help="Render an initial artifact report from the manifest")
    parser.add_argument("--report-output", default="figure-artifact-report.html", help="Report HTML path when --render-report is set")
    parser.add_argument("--judge", action="store_true", help="Run an AI judge over generated figure artifact summaries")
    parser.add_argument("--judge-rubric", default=None, help="Optional extra rubric text for --judge")
    parser.add_argument("--judge-timeout", type=int, default=120, help="Seconds to wait for the judge subagent (default: 120)")
    parser.add_argument("--no-open", action="store_true", help="Do not auto-open rendered report")
    parser.add_argument("--json", action="store_true", help="Print JSON summary")
    args = parser.parse_args()

    task_input = _read_task(args)
    if not task_input.strip():
        raise SystemExit("Task input is empty.")

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
        print(f"[forkprobe] Figure workspace: {result['output_dir']}")
        print(f"[forkprobe] Figure type: {result['figure_type']}")
        print(f"[forkprobe] Pipelines: {', '.join(result['pipelines'])}")
        print(f"[forkprobe] Manifest: {result['manifest_path']}")
        if result.get("report_path"):
            print(f"[forkprobe] Report: {result['report_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
