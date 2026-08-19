"""
Prepare and run image prompt/style comparison packages for ForkProbe v1.1.

This runner does not call any image-generation API. It asks each candidate
pipeline to produce a comparable prompt package. When render mode is
`codex-host`, it writes a local render queue that the host Codex agent can use
to generate optional `rendered.png` files with its own image capability, then
the report can be refreshed.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).parent
PROJECT_DIR = SCRIPT_DIR.parent
CATALOG_PATH = PROJECT_DIR / "catalog" / "image-prompt-skills.json"
DEFAULT_OUTPUT_ROOT = PROJECT_DIR / "outputs" / "image-prompt-runs"
FALSE_VALUES = {"0", "false", "no", "off"}
PROMPT_ARTIFACT_SUFFIXES = {".md", ".json", ".txt", ".yaml", ".yml", ".png", ".jpg", ".jpeg", ".webp"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}

sys.path.insert(0, str(SCRIPT_DIR))
from platform_adapter import Platform, detect_platform, spawn_workspace_agent
from skill_loader import load_skill


@dataclass(frozen=True)
class ImagePromptPipeline:
    id: str
    name: str
    role: str
    summary_zh: str
    summary_en: str
    pipeline_steps: list[str]
    default_families: list[str]
    expected_artifacts: list[str]
    qa_checks: list[str]
    skill_source: str = ""


@dataclass(frozen=True)
class ImagePromptRunResult:
    pipeline_id: str
    output: str
    tokens_used: int
    latency_seconds: float
    error: str | None = None
    token_count_method: str = "provider_reported"


def load_catalog() -> dict[str, Any]:
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


def load_pipeline_registry() -> dict[str, ImagePromptPipeline]:
    catalog = load_catalog()
    pipelines: dict[str, ImagePromptPipeline] = {}
    for item in catalog.get("skills", []):
        pipelines[item["id"]] = ImagePromptPipeline(
            id=str(item["id"]),
            name=str(item.get("name") or item["id"]),
            role=str(item.get("role") or "image_prompt_pipeline"),
            summary_zh=str(item.get("summary_zh") or ""),
            summary_en=str(item.get("summary_en") or ""),
            pipeline_steps=[str(step) for step in item.get("pipeline_steps", [])],
            default_families=[str(family) for family in item.get("default_families", [])],
            expected_artifacts=[str(name) for name in item.get("expected_artifacts", [])],
            qa_checks=[str(check) for check in item.get("qa_checks", [])],
            skill_source=(f"{item.get('source')}#{item.get('subdir')}" if item.get("source") and item.get("subdir") else str(item.get("source") or "")),
        )
    return pipelines


def _compact(text: str) -> str:
    return "".join(text.lower().split())


def detect_image_family(task_text: str) -> str:
    compact = _compact(task_text)
    if any(word in compact for word in ["电商", "主图", "详情图", "商品图", "产品图", "ecommerce", "productphoto", "amazon", "shopify"]):
        return "ecommerce"
    if any(word in compact for word in ["海报", "kv", "keyvisual", "poster", "活动视觉", "主视觉"]):
        return "poster"
    if any(word in compact for word in ["小红书", "封面", "社媒", "thumbnail", "cover", "rednote", "instagram", "tiktok"]):
        return "social"
    if any(word in compact for word in ["头像", "人像", "portrait", "character", "角色", "人物设定", "persona"]):
        return "portrait"
    if any(word in compact for word in ["ppt", "slide", "配图", "插图", "章节页", "presentation"]):
        return "ppt"
    if any(word in compact for word in ["概念图", "概念设计", "游戏", "worldbuilding", "conceptart", "电影", "影视"]):
        return "concept"
    return "general"


def default_pipeline_ids(image_family: str, max_candidates: int = 4) -> list[str]:
    by_family = {
        "ecommerce": ["baseline-image-prompt", "ecommerce-product-prompt", "style-system-prompt", "prompt-as-code"],
        "poster": ["baseline-image-prompt", "poster-key-visual-prompt", "creative-director-prompt", "style-system-prompt"],
        "social": ["baseline-image-prompt", "social-cover-prompt", "creative-director-prompt", "style-system-prompt"],
        "portrait": ["baseline-image-prompt", "portrait-character-prompt", "style-system-prompt", "prompt-as-code"],
        "ppt": ["baseline-image-prompt", "ppt-visual-prompt", "prompt-as-code", "style-system-prompt"],
        "concept": ["baseline-image-prompt", "concept-art-prompt", "creative-director-prompt", "style-system-prompt"],
        "general": ["baseline-image-prompt", "creative-director-prompt", "style-system-prompt", "prompt-as-code"],
    }
    return by_family.get(image_family, by_family["general"])[:max_candidates]


def _slugify(value: str, default: str = "image-prompt") -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip().lower()).strip("-")
    return cleaned or default


def _label_from_skill_source(source: str) -> str:
    base, _, subdir = source.partition("#")
    if subdir:
        return subdir.rstrip("/").split("/")[-1] or "external-image-prompt-skill"
    if base.startswith(("http://", "https://")):
        return base.rstrip("/").split("/")[-1].replace(".git", "") or "external-image-prompt-skill"
    return Path(base).expanduser().name or "external-image-prompt-skill"


def pipeline_from_skill_source(source: str, existing_ids: set[str] | None = None) -> ImagePromptPipeline:
    label = _label_from_skill_source(source)
    base_id = f"skill-{_slugify(label, 'external-image-prompt-skill')}"
    existing_ids = existing_ids or set()
    pipeline_id = base_id
    suffix = 2
    while pipeline_id in existing_ids:
        pipeline_id = f"{base_id}-{suffix}"
        suffix += 1
    return ImagePromptPipeline(
        id=pipeline_id,
        name=f"{label} image prompt package",
        role="external_prompt_skill",
        summary_zh=f"使用外部图片 prompt/style skill `{label}` 生成可比较的 prompt package。",
        summary_en=f"Uses the external image prompt/style skill `{label}` to generate a comparable prompt package.",
        pipeline_steps=[source, "prompt-package", "optional-render-queue"],
        default_families=["general", "ecommerce", "poster", "social", "portrait", "ppt", "concept"],
        expected_artifacts=["prompt.md", "style-card.md", "composition.md", "negative-prompt.md", "render-notes.md", "summary.md"],
        qa_checks=["prompt_specific", "style_coherent", "portable_to_renderers", "constraints_explicit"],
        skill_source=source,
    )


def build_pipeline_registry(skill_sources: list[str] | None = None) -> tuple[dict[str, ImagePromptPipeline], list[str]]:
    pipelines = load_pipeline_registry()
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
    return suffix.upper() if suffix else "file"


def _read_optional(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()
    except UnicodeDecodeError:
        return ""


def _load_run_result(candidate_dir: Path) -> dict[str, Any]:
    path = candidate_dir / "run-result.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _preview_for(path: Path, artifact_dir: Path, output_dir: Path) -> str:
    if path.suffix.lower() in IMAGE_SUFFIXES:
        return _relative(path, output_dir)
    for preview_name in ("rendered.png", "rendered.jpg", "rendered.webp"):
        preview = artifact_dir / preview_name
        if preview.exists():
            return _relative(preview, output_dir)
    return ""


def collect_candidate_artifacts(candidate_dir: Path, output_dir: Path) -> list[dict[str, Any]]:
    artifact_dir = candidate_dir / "artifacts"
    if not artifact_dir.exists():
        return []
    artifacts: list[dict[str, Any]] = []
    for path in sorted(p for p in artifact_dir.rglob("*") if p.is_file()):
        if path.name == ".gitkeep" or path.suffix.lower() not in PROMPT_ARTIFACT_SUFFIXES:
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


def candidate_summary(pipeline: ImagePromptPipeline, candidate_dir: Path) -> str:
    parts = [pipeline.summary_zh]
    for filename, title in (
        ("summary.md", "Summary"),
        ("prompt.md", "Prompt"),
        ("style-card.md", "Style Card"),
        ("composition.md", "Composition"),
        ("negative-prompt.md", "Negative Prompt"),
        ("render-notes.md", "Render Notes"),
        ("qa.json", "QA"),
        ("runner-output.md", "Runner output"),
    ):
        text = _read_optional(candidate_dir / filename) or _read_optional(candidate_dir / "artifacts" / filename)
        if text:
            parts.append(f"\n\n## {title}\n{text}")
    return "\n".join(parts)


def estimate_candidate_tokens(
    task_input: str,
    pipeline: ImagePromptPipeline,
    candidate_dir: Path,
    summary: str,
    artifacts: list[dict[str, Any]],
    run_result: dict[str, Any],
) -> int:
    from compare import estimate_text_tokens

    prompt = _read_optional(candidate_dir / "RUN_PROMPT.md") or _read_optional(candidate_dir / "INSTRUCTIONS.md")
    artifact_text = "\n".join(
        f"{artifact.get('label') or artifact.get('path') or ''} {artifact.get('kind') or ''}"
        for artifact in artifacts
    )
    visible_text = "\n\n".join(
        part for part in [
            prompt,
            str(run_result.get("output") or ""),
            pipeline.summary_en,
            summary,
            artifact_text,
            task_input,
        ]
        if part
    )
    return estimate_text_tokens(visible_text)


def build_pipeline_instructions(
    task_input: str,
    pipeline: ImagePromptPipeline,
    candidate_dir: Path,
    reference_images: list[Path] | None = None,
) -> str:
    artifact_dir = candidate_dir / "artifacts"
    expected = "\n".join(f"- `{name}`" for name in pipeline.expected_artifacts)
    qa = "\n".join(f"- {check}" for check in pipeline.qa_checks)
    steps = " -> ".join(pipeline.pipeline_steps)
    refs = "\n".join(f"- `{path}`" for path in reference_images or []) or "- none provided"
    return f"""# {pipeline.name}

## Goal

Generate an image prompt and visual style package for the same original task as every other ForkProbe candidate.

This is a prompt/style pipeline. Do not call image-generation APIs and do not render images from inside this runner.

## Original Task

{task_input}

## Pipeline

{steps}

## Reference Images

{refs}

If reference images are provided but you cannot inspect them, write `reference-usage.md` that explains how the downstream renderer or user should use them instead of inventing visual facts.

## Output Directory

Write all candidate outputs under:

`{artifact_dir}`

## Required Prompt Package

{expected}

Use plain Markdown for human-readable files. Keep the final prompt copyable.

## QA Checks

{qa}

## File Requirements

- `prompt.md`: final image prompt, ready to paste into a renderer.
- `style-card.md`: reusable style direction, visual vocabulary, palette, material, lighting, and mood.
- `composition.md`: layout, camera/framing, subject placement, depth, negative space, and safe zones.
- `negative-prompt.md`: what to avoid, including generic AI artifacts, wrong text, clutter, unsafe brand copying, and visual contradictions.
- `render-notes.md`: recommended aspect ratio, quality/draft settings, how to use references, and renderer-specific caveats without naming API keys.
- `summary.md`: what this candidate optimizes for, what makes it different, and known risks.

Optional but useful:
- `prompt.json`: structured prompt-as-code version.
- `reference-usage.md`: how references should influence style without copying.
- `qa.json`: checklist result for this candidate's own prompt package.
"""


def load_pipeline_skill_prompt(pipeline: ImagePromptPipeline) -> str:
    if not pipeline.skill_source:
        return ""
    try:
        skill = load_skill(skill_id=pipeline.id, source=pipeline.skill_source)
        return skill.to_system_prompt()
    except Exception as exc:  # noqa: BLE001 - user-facing preflight should keep going
        return f"[Could not load external skill source {pipeline.skill_source!r}: {type(exc).__name__}: {exc}]"


def build_candidate_run_prompt(
    task_input: str,
    pipeline: ImagePromptPipeline,
    candidate_dir: Path,
    reference_images: list[Path] | None = None,
) -> str:
    instructions = (candidate_dir / "INSTRUCTIONS.md").read_text(encoding="utf-8")
    artifact_dir = candidate_dir / "artifacts"
    skill_prompt = load_pipeline_skill_prompt(pipeline)
    skill_section = ""
    if skill_prompt:
        skill_section = f"""
## External Skill Instructions

This pipeline is backed by an external prompt/style skill. Apply these instructions while producing the prompt package:

{skill_prompt}
"""
    return f"""You are running one isolated ForkProbe image-prompt candidate.

Your job is to generate prompt/style artifacts, not to compare candidates and not to render images.

Hard requirements:
- Write all generated files under `{artifact_dir}`.
- Create or update `{candidate_dir / 'summary.md'}` and `{artifact_dir / 'summary.md'}`.
- Do not call image APIs, browser tools, or external rendering services.
- Do not ask the user follow-up questions.
- Do not modify files outside `{candidate_dir}` unless required by a local tool cache.
- After the files are written, respond with a concise completion summary and stop.

{skill_section}

{instructions}

## Original task, repeated for convenience

{task_input}
"""


def _render_mode_status(render_mode: str, platform: Platform) -> dict[str, Any]:
    selected = render_mode
    if render_mode == "auto":
        selected = "codex-host" if platform == Platform.CODEX else "prompt-only"
    return {
        "requested": render_mode,
        "selected": selected,
        "platform": platform.value,
        "runner_calls_image_api": False,
        "status": "queue_written" if selected == "codex-host" else "prompt_only",
    }


def _first_existing_text(candidate_dir: Path, *relative_paths: str) -> str:
    for relative in relative_paths:
        text = _read_optional(candidate_dir / relative)
        if text:
            return text
    return ""


def build_render_queue(
    output_dir: Path,
    pipeline_ids: list[str],
    pipeline_registry: dict[str, ImagePromptPipeline],
    render_mode_status: dict[str, Any],
) -> dict[str, Any]:
    selected_mode = str(render_mode_status.get("selected") or "prompt-only")
    queue: dict[str, Any] = {
        "schema_version": "image-render-queue-v1.1",
        "render_mode": selected_mode,
        "runner_calls_image_api": False,
        "instructions": (
            "If running inside Codex with image generation available, render each item using the host image tool, "
            "write the image to output_path, then rerun image_prompt_artifact.py with --refresh-artifacts --render-report."
        ),
        "items": [],
    }
    if selected_mode != "codex-host":
        return queue
    for pipeline_id in pipeline_ids:
        pipeline = pipeline_registry[pipeline_id]
        candidate_dir = output_dir / "candidates" / pipeline.id
        artifact_dir = candidate_dir / "artifacts"
        prompt_text = _first_existing_text(candidate_dir, "artifacts/prompt.md", "prompt.md")
        style_text = _first_existing_text(candidate_dir, "artifacts/style-card.md", "style-card.md")
        composition_text = _first_existing_text(candidate_dir, "artifacts/composition.md", "composition.md")
        negative_text = _first_existing_text(candidate_dir, "artifacts/negative-prompt.md", "negative-prompt.md")
        item = {
            "candidate_id": pipeline.id,
            "candidate_name": pipeline.name,
            "prompt_path": _relative(artifact_dir / "prompt.md", output_dir),
            "style_card_path": _relative(artifact_dir / "style-card.md", output_dir),
            "composition_path": _relative(artifact_dir / "composition.md", output_dir),
            "negative_prompt_path": _relative(artifact_dir / "negative-prompt.md", output_dir),
            "output_path": _relative(artifact_dir / "rendered.png", output_dir),
            "prompt_text": prompt_text,
            "style_card": style_text,
            "composition": composition_text,
            "negative_prompt": negative_text,
        }
        queue["items"].append(item)
        (artifact_dir / "render-request.json").write_text(json.dumps(item, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "render-queue.json").write_text(json.dumps(queue, ensure_ascii=False, indent=2), encoding="utf-8")
    return queue


def build_manifest(
    task_input: str,
    output_dir: Path,
    pipeline_ids: list[str],
    image_family: str,
    render_mode_status: dict[str, Any],
    pipeline_registry: dict[str, ImagePromptPipeline] | None = None,
) -> dict[str, Any]:
    pipeline_registry = pipeline_registry or load_pipeline_registry()
    candidates = []
    for pipeline_id in pipeline_ids:
        pipeline = pipeline_registry[pipeline_id]
        candidate_dir = output_dir / "candidates" / pipeline.id
        run_result = _load_run_result(candidate_dir)
        summary = candidate_summary(pipeline, candidate_dir)
        artifacts = collect_candidate_artifacts(candidate_dir, output_dir)
        token_count_method = str(run_result.get("token_count_method") or "provider_reported")
        provider_tokens = int(run_result.get("tokens_used") or 0) if token_count_method == "provider_reported" else 0
        estimated_tokens = estimate_candidate_tokens(task_input, pipeline, candidate_dir, summary, artifacts, run_result)
        rendered = candidate_dir / "artifacts" / "rendered.png"
        candidates.append({
            "id": pipeline.id,
            "name": pipeline.name,
            "category": "image-prompt-artifact",
            "summary": summary,
            "workdir": _relative(candidate_dir, output_dir),
            "pipeline_steps": list(pipeline.pipeline_steps),
            "skill_source": pipeline.skill_source,
            "expected_artifacts": list(pipeline.expected_artifacts),
            "qa_checks": list(pipeline.qa_checks),
            "rendered_image_path": _relative(rendered, output_dir) if rendered.exists() else "",
            "artifacts": artifacts,
            "tokens_used": provider_tokens,
            "provider_tokens_used": provider_tokens,
            "token_count_method": token_count_method,
            "estimated_tokens_used": estimated_tokens,
            "latency_seconds": float(run_result.get("latency_seconds") or 0.0),
            "error": run_result.get("error"),
        })
    render_queue = build_render_queue(output_dir, pipeline_ids, pipeline_registry, render_mode_status)
    return {
        "schema_version": "image-prompt-artifact-v1.1",
        "deliverable_type": "image_prompt",
        "image_family": image_family,
        "task_input_path": "task.md",
        "duration_seconds": 0,
        "render_mode": render_mode_status,
        "render_queue_path": "render-queue.json" if render_queue.get("items") else "",
        "artifact_contract": {
            "required_prompt_package": ["prompt.md", "style-card.md", "composition.md", "negative-prompt.md", "render-notes.md", "summary.md"],
            "optional_rendered_image": "artifacts/rendered.png",
            "runner_calls_image_api": False,
        },
        "candidates": candidates,
    }


def build_artifact_judge_results(manifest: dict[str, Any]) -> list[Any]:
    from compare import RunResult

    results = []
    for candidate in manifest.get("candidates", []):
        artifacts = candidate.get("artifacts", [])
        runner_error = candidate.get("error")
        artifact_lines = [
            f"- {artifact.get('label') or artifact.get('path') or 'artifact'} ({artifact.get('kind') or 'file'})"
            for artifact in artifacts
        ]
        output = (
            f"{candidate.get('summary') or ''}\n\n"
            f"## Generated prompt artifacts\n" + ("\n".join(artifact_lines) or "No generated artifacts found.") + "\n\n"
            f"## Expected artifacts\n" + "\n".join(f"- {item}" for item in candidate.get("expected_artifacts", [])) + "\n\n"
            f"## QA checks\n" + "\n".join(f"- {item}" for item in candidate.get("qa_checks", []))
        )
        if candidate.get("rendered_image_path"):
            output += f"\n\n## Render validation\nRendered image present: {candidate['rendered_image_path']}"
        if runner_error:
            output += f"\n\n## Runner issue\n{runner_error}"
        results.append(RunResult(
            skill_id=str(candidate.get("id") or candidate.get("name") or "candidate"),
            skill_name=str(candidate.get("name") or candidate.get("id") or "candidate"),
            skill_author=str(candidate.get("author") or ""),
            skill_category=str(candidate.get("category") or "image-prompt-artifact"),
            output=output,
            tokens_used=int(candidate.get("tokens_used") or 0),
            latency_seconds=float(candidate.get("latency_seconds") or 0.0),
            estimated_tokens_used=int(candidate.get("estimated_tokens_used") or 0),
            provider_tokens_used=int(candidate.get("provider_tokens_used") or candidate.get("tokens_used") or 0),
            error=None if artifacts else runner_error,
        ))
    return results


def run_artifact_judge(task_input: str, manifest: dict[str, Any], rubric: str | None = None, timeout: int = 120) -> dict[str, Any]:
    from compare import run_judge

    rubric_text = rubric or (
        "Evaluate image prompt/style packages. Prefer candidates with specific visual direction, reusable style cards, "
        "clear composition, strong negative constraints, renderer-portable prompts, honest render notes, and optional rendered-image validation when present. "
        "Do not assume image quality unless a rendered image artifact is present in the manifest."
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
    render_mode: str = "auto",
    reference_images: list[Path] | None = None,
) -> dict[str, Any]:
    output_dir = output_dir.expanduser().resolve()
    image_family = detect_image_family(task_input)
    platform = detect_platform()
    render_mode_status = _render_mode_status(render_mode, platform)
    pipeline_registry, dynamic_ids = build_pipeline_registry(skill_sources)
    selected_ids = list(pipeline_ids or default_pipeline_ids(image_family, max_candidates=max_candidates))
    for dynamic_id in dynamic_ids:
        if dynamic_id not in selected_ids:
            selected_ids.append(dynamic_id)
    unknown = [pipeline_id for pipeline_id in selected_ids if pipeline_id not in pipeline_registry]
    if unknown:
        raise KeyError(f"Unknown image prompt pipeline(s): {', '.join(unknown)}")

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "task.md").write_text(task_input, encoding="utf-8")
    references = [path.expanduser().resolve() for path in reference_images or []]
    if references:
        (output_dir / "references.json").write_text(
            json.dumps([str(path) for path in references], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    for pipeline_id in selected_ids:
        pipeline = pipeline_registry[pipeline_id]
        candidate_dir = output_dir / "candidates" / pipeline.id
        artifact_dir = candidate_dir / "artifacts"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        (artifact_dir / ".gitkeep").write_text("", encoding="utf-8")
        (candidate_dir / "INSTRUCTIONS.md").write_text(
            build_pipeline_instructions(task_input, pipeline, candidate_dir, references),
            encoding="utf-8",
        )

    manifest = build_manifest(task_input, output_dir, selected_ids, image_family, render_mode_status, pipeline_registry)
    manifest_path = output_dir / "artifact-manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "output_dir": str(output_dir),
        "manifest_path": str(manifest_path),
        "image_family": image_family,
        "pipelines": selected_ids,
        "skill_sources": list(skill_sources or []),
        "render_mode": render_mode_status,
        "manifest": manifest,
    }


def run_candidate_agent(
    task_input: str,
    output_dir: Path,
    pipeline: ImagePromptPipeline,
    timeout: int,
    platform: Platform,
    reference_images: list[Path] | None = None,
) -> ImagePromptRunResult:
    candidate_dir = output_dir / "candidates" / pipeline.id
    prompt = build_candidate_run_prompt(task_input, pipeline, candidate_dir, reference_images)
    (candidate_dir / "RUN_PROMPT.md").write_text(prompt, encoding="utf-8")
    started = time.time()
    result = spawn_workspace_agent(
        platform=platform,
        prompt=prompt,
        cwd=output_dir,
        timeout_seconds=timeout,
        sandbox="workspace-write",
        writable_dirs=[candidate_dir],
    )
    elapsed = time.time() - started
    (candidate_dir / "runner-output.md").write_text(result.output or "", encoding="utf-8")
    run_result = ImagePromptRunResult(
        pipeline_id=pipeline.id,
        output=result.output or "",
        tokens_used=int(result.tokens_used or 0),
        latency_seconds=float(result.latency_seconds or elapsed),
        error=result.error,
        token_count_method=result.token_count_method,
    )
    (candidate_dir / "run-result.json").write_text(json.dumps(asdict(run_result), ensure_ascii=False, indent=2), encoding="utf-8")
    return run_result


def run_parallel(
    task_input: str,
    output_dir: Path,
    pipeline_ids: list[str],
    pipeline_registry: dict[str, ImagePromptPipeline],
    timeout: int,
    max_workers: int,
    platform: Platform,
    reference_images: list[Path] | None = None,
) -> dict[str, Any]:
    results: dict[str, ImagePromptRunResult] = {}
    workers = max(1, min(max_workers, len(pipeline_ids)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                run_candidate_agent,
                task_input,
                output_dir,
                pipeline_registry[pipeline_id],
                timeout,
                platform,
                reference_images,
            ): pipeline_id
            for pipeline_id in pipeline_ids
        }
        for future in as_completed(futures):
            pipeline_id = futures[future]
            try:
                results[pipeline_id] = future.result()
            except Exception as exc:  # noqa: BLE001 - preserve per-candidate failures
                candidate_dir = output_dir / "candidates" / pipeline_id
                run_result = ImagePromptRunResult(
                    pipeline_id=pipeline_id,
                    output="",
                    tokens_used=0,
                    latency_seconds=0.0,
                    error=f"{type(exc).__name__}: {exc}",
                )
                (candidate_dir / "run-result.json").write_text(json.dumps(asdict(run_result), ensure_ascii=False, indent=2), encoding="utf-8")
                results[pipeline_id] = run_result
    return {key: asdict(value) for key, value in results.items()}


def _read_task(args: argparse.Namespace) -> tuple[str, str]:
    if args.input:
        path = Path(args.input)
        return path.read_text(encoding="utf-8"), path.stem
    if args.text:
        return args.text, "image-prompt-task"
    text = sys.stdin.read()
    return text, "image-prompt-task"


def _default_output_dir(label: str) -> Path:
    timestamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    return DEFAULT_OUTPUT_ROOT / f"{timestamp}-{_slugify(label, 'image-prompt-task')}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run image prompt/style artifact comparisons")
    parser.add_argument("--input", help="Path to task input")
    parser.add_argument("--text", help="Task text when --input is omitted")
    parser.add_argument("--output-dir", help="Run output directory")
    parser.add_argument("--pipeline", action="append", default=[], help="Image prompt pipeline id to include")
    parser.add_argument("--skill-source", action="append", default=[], help="External image prompt/style skill source path or repo#subdir")
    parser.add_argument("--reference-image", action="append", default=[], help="Reference image path to record for prompt/style candidates")
    parser.add_argument("--max-candidates", type=int, default=4, help="Default candidate count when --pipeline is omitted")
    parser.add_argument("--render-mode", choices=["auto", "prompt-only", "codex-host", "user-backfill"], default="auto", help="Rendering validation mode; the runner never calls image APIs")
    parser.add_argument("--confirmed", action="store_true", help="Required before --run; confirms the user approved the shortlist")
    parser.add_argument("--run", action="store_true", help="Run each prompt/style candidate agent")
    parser.add_argument("--timeout", type=int, default=900, help="Seconds per candidate")
    parser.add_argument("--max-workers", type=int, default=3, help="Parallel candidate workers")
    parser.add_argument("--judge", action="store_true", help="Run AI judge on prompt package summaries")
    parser.add_argument("--judge-rubric", help="Optional judge rubric override")
    parser.add_argument("--judge-timeout", type=int, default=120, help="Judge timeout seconds")
    parser.add_argument("--render-report", action="store_true", help="Render the artifact comparison report")
    parser.add_argument("--report-output", default="image-prompt-artifact-report.html", help="Report path inside output dir or absolute path")
    parser.add_argument("--refresh-artifacts", action="store_true", help="Refresh manifest/report after manual rendered-image backfill")
    parser.add_argument("--no-open", action="store_true", help="Do not open report")
    parser.add_argument("--no-server", action="store_true", help="Do not start local winner continuation server")
    parser.add_argument("--json", action="store_true", help="Print JSON summary")
    args = parser.parse_args()

    if args.run and not args.confirmed:
        print(
            "[forkprobe] Refusing to run image prompt candidates without candidate confirmation. "
            "First run scripts/recommend.py, show the shortlist to the user, then rerun with --confirmed.",
            file=sys.stderr,
        )
        return 2

    task_input, label = _read_task(args)
    output_dir = Path(args.output_dir).expanduser().resolve() if args.output_dir else _default_output_dir(label)
    reference_images = [Path(path) for path in args.reference_image]
    platform = detect_platform()
    render_mode_status = _render_mode_status(args.render_mode, platform)
    pipeline_registry, dynamic_ids = build_pipeline_registry(args.skill_source)

    if args.refresh_artifacts and not (output_dir / "task.md").exists():
        print("[forkprobe] --refresh-artifacts requires an existing output directory with task.md", file=sys.stderr)
        return 2

    if args.refresh_artifacts:
        task_input = (output_dir / "task.md").read_text(encoding="utf-8")
        manifest_path = output_dir / "artifact-manifest.json"
        old_manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
        pipeline_ids = [str(candidate.get("id")) for candidate in old_manifest.get("candidates", [])]
        image_family = str(old_manifest.get("image_family") or detect_image_family(task_input))
        manifest = build_manifest(task_input, output_dir, pipeline_ids, image_family, render_mode_status, pipeline_registry)
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        result: dict[str, Any] = {
            "output_dir": str(output_dir),
            "manifest_path": str(manifest_path),
            "image_family": image_family,
            "pipelines": pipeline_ids,
            "render_mode": render_mode_status,
            "manifest": manifest,
        }
    else:
        result = create_workspace(
            task_input=task_input,
            output_dir=output_dir,
            pipeline_ids=args.pipeline or None,
            skill_sources=args.skill_source,
            max_candidates=args.max_candidates,
            render_mode=args.render_mode,
            reference_images=reference_images,
        )
        pipeline_ids = list(result["pipelines"])
        if args.run:
            started = time.time()
            run_results = run_parallel(
                task_input=task_input,
                output_dir=output_dir,
                pipeline_ids=pipeline_ids,
                pipeline_registry=pipeline_registry,
                timeout=args.timeout,
                max_workers=args.max_workers,
                platform=platform,
                reference_images=[path.expanduser().resolve() for path in reference_images],
            )
            manifest = build_manifest(
                task_input,
                output_dir,
                pipeline_ids,
                str(result["image_family"]),
                render_mode_status,
                pipeline_registry,
            )
            manifest["duration_seconds"] = time.time() - started
            manifest_path = output_dir / "artifact-manifest.json"
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            result["manifest"] = manifest
            result["run_results"] = run_results

    if args.judge:
        manifest = result["manifest"]
        manifest["judge"] = run_artifact_judge(task_input, manifest, rubric=args.judge_rubric, timeout=args.judge_timeout)
        Path(result["manifest_path"]).write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        result["manifest"] = manifest

    if args.render_report or args.run or args.judge:
        from render_artifact_report import render_session_from_manifest

        report_output = Path(args.report_output)
        if not report_output.is_absolute():
            report_output = output_dir / report_output
        session = render_session_from_manifest(
            manifest_path=Path(result["manifest_path"]),
            output_path=report_output,
            auto_open=not args.no_open,
            no_server=args.no_server,
        )
        result["report"] = session

    if args.json:
        print(json.dumps({k: v for k, v in result.items() if k != "manifest"}, ensure_ascii=False, indent=2))
    else:
        print(f"[forkprobe] Image prompt run directory: {result['output_dir']}")
        print(f"[forkprobe] Manifest: {result['manifest_path']}")
        render_queue_path = Path(result["output_dir"]) / "render-queue.json"
        if render_queue_path.exists():
            print(f"[forkprobe] Render queue: {render_queue_path}")
        if result.get("report"):
            print(f"[forkprobe] Report: {result['report']['report_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
