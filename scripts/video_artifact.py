"""
Run finished-video comparisons for product promos, motion graphics, and
talking-head rough cuts.

Every candidate receives the same brief and source assets, writes into an
isolated artifact directory, and is normalized into a shared package. ForkProbe
uses ffprobe/ffmpeg for common media QA and renders the candidates in the same
local HTML report used by other artifact modes.
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
CATALOG_PATH = PROJECT_DIR / "catalog" / "video-artifact-skills.json"
DEFAULT_OUTPUT_ROOT = PROJECT_DIR / "outputs" / "video-runs"
FALSE_VALUES = {"0", "false", "no", "off"}
VIDEO_SUFFIXES = {".mp4", ".webm", ".mov", ".m4v"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
ARTIFACT_SUFFIXES = VIDEO_SUFFIXES | IMAGE_SUFFIXES | {
    ".srt", ".vtt", ".md", ".txt", ".json", ".csv", ".edl", ".xml", ".zip", ".html", ".pdf"
}


@dataclass(frozen=True)
class VideoPipeline:
    id: str
    name: str
    author: str
    role: str
    summary_zh: str
    summary_en: str
    pipeline_steps: list[str]
    default_families: list[str]
    expected_artifacts: list[str]
    qa_checks: list[str]
    skill_source: str = ""
    maturity: str = "stable"
    requires: list[str] | None = None


@dataclass(frozen=True)
class VideoRunResult:
    pipeline_id: str
    output: str
    tokens_used: int
    latency_seconds: float
    error: str | None = None


def _compact(text: str) -> str:
    return "".join(text.lower().split())


def _slugify(value: str, default: str = "video-run") -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip().lower()).strip("-")
    return cleaned or default


def split_skill_source(source: str) -> tuple[str, str | None]:
    if "#" not in source:
        return source, None
    base, _, fragment = source.partition("#")
    subdir = fragment.strip().strip("/")
    return base, subdir or None


def load_video_catalog() -> dict[str, Any]:
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


def _pipeline_from_catalog(entry: dict[str, Any]) -> VideoPipeline:
    source = str(entry.get("source") or "")
    subdir = str(entry.get("subdir") or "").strip().strip("/")
    if source and subdir:
        source = f"{source}#{subdir}"
    return VideoPipeline(
        id=str(entry["id"]),
        name=str(entry["name"]),
        author=str(entry.get("author") or ""),
        role=str(entry.get("role") or "video_pipeline"),
        summary_zh=str(entry.get("summary_zh") or ""),
        summary_en=str(entry.get("summary_en") or ""),
        pipeline_steps=[str(step) for step in entry.get("pipeline_steps") or []],
        default_families=[str(value) for value in entry.get("default_families") or []],
        expected_artifacts=[str(value) for value in entry.get("expected_artifacts") or []],
        qa_checks=[str(value) for value in entry.get("qa_checks") or []],
        skill_source=source,
        maturity=str(entry.get("maturity") or "stable"),
        requires=[str(value) for value in entry.get("requires") or []],
    )


def built_in_pipelines() -> dict[str, VideoPipeline]:
    return {
        pipeline.id: pipeline
        for pipeline in (
            _pipeline_from_catalog(entry)
            for entry in load_video_catalog().get("pipelines", [])
        )
    }


VIDEO_PIPELINES = built_in_pipelines()


def detect_video_type(task_text: str) -> str:
    compact = _compact(task_text)
    if any(word in compact for word in [
        "口播", "粗剪", "删停顿", "删除停顿", "删口误", "删除口误", "talkinghead",
        "roughcut", "jumpcut", "podcastcut", "采访剪辑", "访谈剪辑",
    ]):
        return "talking_head_cut"
    if any(word in compact for word in [
        "动效", "动态图形", "motiongraphics", "kinetictype", "数据动画", "图表动画",
        "字幕动效", "logosting", "lowerthird", "信息动效",
    ]):
        return "motion_graphics"
    return "product_promo"


def default_pipeline_ids(video_type: str, max_candidates: int | None = None) -> list[str]:
    ordered = {
        "product_promo": [
            "baseline-remotion-agent",
            "hyperframes-product-launch",
            "video-shotcraft",
        ],
        "motion_graphics": [
            "baseline-remotion-motion",
            "hyperframes-motion-graphics",
            "remotion-bits-enhanced",
        ],
        "talking_head_cut": [
            "auto-editor",
            "maxazure-video-editing",
            "video-use-cut-only",
            "chengfeng-cut-talking-head",
        ],
    }.get(video_type, [])
    return ordered if max_candidates is None else ordered[:max_candidates]


def _label_from_skill_source(source: str) -> str:
    base, subdir = split_skill_source(source)
    if subdir:
        return subdir.rstrip("/").split("/")[-1] or "external-video-skill"
    if base.startswith(("http://", "https://")):
        return base.rstrip("/").split("/")[-1].replace(".git", "") or "external-video-skill"
    return Path(base).expanduser().name or "external-video-skill"


def pipeline_from_skill_source(source: str, existing_ids: set[str] | None = None) -> VideoPipeline:
    label = _label_from_skill_source(source)
    base_id = f"skill-{_slugify(label, 'external-video-skill')}"
    existing_ids = existing_ids or set()
    pipeline_id = base_id
    suffix = 2
    while pipeline_id in existing_ids:
        pipeline_id = f"{base_id}-{suffix}"
        suffix += 1
    return VideoPipeline(
        id=pipeline_id,
        name=f"{label} video pipeline",
        author="",
        role="external_video_skill",
        summary_zh=f"使用外部视频 skill `{label}` 生成可比较的视频成品。",
        summary_en=f"Uses the external video skill `{label}` to produce a comparable finished video.",
        pipeline_steps=[source, "video-render", "video-qa"],
        default_families=["product_promo", "motion_graphics", "talking_head_cut"],
        expected_artifacts=["video.mp4", "poster.png", "source.zip", "qa.json", "summary.md"],
        qa_checks=["video_decodes", "brief_fidelity", "source_delivered"],
        skill_source=source,
        maturity="external",
        requires=[],
    )


def build_pipeline_registry(skill_sources: list[str] | None = None) -> tuple[dict[str, VideoPipeline], list[str]]:
    pipelines = dict(VIDEO_PIPELINES)
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


def _read_optional(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return ""


def _kind_for(path: Path) -> str:
    return path.suffix.lstrip(".").upper() or "FILE"


def _load_run_result(candidate_dir: Path) -> dict[str, Any]:
    try:
        return json.loads((candidate_dir / "run-result.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _asset_records(asset_paths: list[str] | None) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for value in asset_paths or []:
        path = Path(value).expanduser().resolve()
        records.append({
            "path": str(path),
            "exists": path.exists(),
            "kind": "directory" if path.is_dir() else (path.suffix.lstrip(".").lower() or "file"),
        })
    return records


def _source_video_paths(assets: list[dict[str, Any]]) -> list[Path]:
    paths: list[Path] = []
    for asset in assets:
        path = Path(str(asset.get("path") or ""))
        if path.is_file() and path.suffix.lower() in VIDEO_SUFFIXES:
            paths.append(path)
        elif path.is_dir():
            paths.extend(sorted(p for p in path.rglob("*") if p.is_file() and p.suffix.lower() in VIDEO_SUFFIXES))
    return paths


def load_pipeline_skill_prompt(pipeline: VideoPipeline) -> str:
    if not pipeline.skill_source:
        return ""
    if os.environ.get("FORKPROBE_VIDEO_LOAD_SKILL_PROMPTS", "1").lower() in FALSE_VALUES:
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
            "Continue with the built-in pipeline contract and record this fallback in summary.md."
        )


def _family_contract(video_type: str) -> str:
    if video_type == "talking_head_cut":
        return """This is a rough-cut task over existing footage.
- Preserve the speaker's meaning, chronology, voice, and picture.
- Remove only silence, false starts, repetitions, obvious mistakes, and explicitly unwanted passages.
- Do not add B-roll, generated scenes, music, visual redesign, or script rewriting.
- `cut-list.json` must make every removal auditable with source start/end time and reason.
- Preserve source audio sync and provide `transcript.md` plus `subtitles.srt`."""
    if video_type == "motion_graphics":
        return """This is a motion-graphics task.
- Preserve every supplied fact, number, label, and brand token.
- Prefer deterministic, frame-addressable animation and readable typography.
- Do not pad the result with a product-story narrative unless the brief requests one.
- Record timing, aspect ratio, frame rate, animation system, and audio policy in `motion-spec.md`."""
    return """This is a finished product-promo task.
- Build a clear hook, problem/value story, authentic product proof, and closing CTA.
- Use real supplied product screenshots or page captures whenever available.
- Deliver a coherent edit with readable captions and intentional audio; do not hide the actual product behind atmospheric footage.
- `script.md` and `storyboard.md` must match the rendered video."""


def build_pipeline_instructions(
    task_input: str,
    pipeline: VideoPipeline,
    candidate_dir: Path,
    video_type: str,
    assets: list[dict[str, Any]],
) -> str:
    artifact_dir = candidate_dir / "artifacts"
    expected = "\n".join(f"- `{name}`" for name in pipeline.expected_artifacts)
    asset_lines = "\n".join(
        f"- `{asset['path']}` ({asset['kind']}, {'available' if asset['exists'] else 'missing'})"
        for asset in assets
    ) or "- No external asset path supplied."
    return f"""# {pipeline.name}

## Goal

Generate one finished `{video_type}` candidate from the same task and assets used by every ForkProbe pipeline.

## Original Task

{task_input}

## Pipeline

{" -> ".join(pipeline.pipeline_steps)}

Role: `{pipeline.role}`
Maturity: `{pipeline.maturity}`
External skill source: `{pipeline.skill_source or "built-in baseline"}`

## Shared Source Assets

{asset_lines}

Treat source assets as read-only. Do not overwrite or move them.

## Output Directory

Write all generated video outputs under:

`{artifact_dir}`

## Expected Package

{expected}

The primary playable result must be named `video.mp4`. ForkProbe may create `poster.png` and overwrite `qa.json` with shared ffprobe-based checks after your run.

## Scene Contract

{_family_contract(video_type)}

## Delivery Rules

- Keep all candidate-specific source code under `artifacts/source/` and package it as `source.zip` when practical.
- Do not modify files outside `{candidate_dir}` except package-manager or renderer caches.
- Do not ask the user follow-up questions during this isolated run.
- Do not compare against other candidates.
- Write a concise `summary.md` describing the creative/editing decisions, dependencies, missing inputs, and limitations.
- Prefer local assets and record any generated or external media provenance.
"""


def build_candidate_run_prompt(
    task_input: str,
    pipeline: VideoPipeline,
    candidate_dir: Path,
    video_type: str,
    assets: list[dict[str, Any]],
) -> str:
    instructions = (candidate_dir / "INSTRUCTIONS.md").read_text(encoding="utf-8")
    skill_prompt = load_pipeline_skill_prompt(pipeline)
    video_output = candidate_dir / "artifacts" / "video.mp4"
    skill_section = f"""
## External Skill Instructions

Apply the following upstream skill instructions, while the ForkProbe scene contract and output paths remain mandatory:

{skill_prompt}
""" if skill_prompt else ""
    return f"""You are running one isolated ForkProbe finished-video candidate.

Generate the artifact package and stop. Do not return only a plan.

Hard requirements:
- Write the playable result to `{video_output}`.
- Create or update `{candidate_dir / "summary.md"}`.
- Keep every output under `{candidate_dir}`.
- Respect the `{video_type}` scene contract.
- Render a real, decodable video rather than a placeholder.
- After files are written, respond with a concise completion summary.

{skill_section}

{instructions}

## Original task, repeated for convenience

{task_input}
"""


def _probe_video(path: Path) -> dict[str, Any]:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe or not path.exists():
        return {}
    cmd = [
        ffprobe, "-v", "error",
        "-show_entries", "format=duration,size,format_name:stream=index,codec_type,codec_name,width,height,r_frame_rate",
        "-of", "json", str(path),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if proc.returncode != 0:
            return {"error": proc.stderr.strip() or f"ffprobe exited {proc.returncode}"}
        payload = json.loads(proc.stdout)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}
    streams = payload.get("streams") or []
    video_stream = next((stream for stream in streams if stream.get("codec_type") == "video"), {})
    format_info = payload.get("format") or {}
    duration = float(format_info.get("duration") or 0.0)
    return {
        "duration_seconds": round(duration, 3),
        "width": int(video_stream.get("width") or 0),
        "height": int(video_stream.get("height") or 0),
        "fps": str(video_stream.get("r_frame_rate") or ""),
        "video_codec": str(video_stream.get("codec_name") or ""),
        "has_audio": any(stream.get("codec_type") == "audio" for stream in streams),
        "audio_codec": str(next((stream.get("codec_name") for stream in streams if stream.get("codec_type") == "audio"), "") or ""),
        "format": str(format_info.get("format_name") or ""),
        "size_bytes": int(format_info.get("size") or path.stat().st_size),
    }


def _sample_frame_variation(path: Path) -> dict[str, Any]:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg or not path.exists():
        return {
            "sampled_frames": 0,
            "unique_frames": 0,
            "error": "ffmpeg not found" if not ffmpeg else "video missing",
        }
    cmd = [
        ffmpeg, "-v", "error", "-i", str(path),
        "-map", "0:v:0", "-an",
        "-vf", "fps=2,scale=160:-2",
        "-frames:v", "24",
        "-f", "framemd5", "-",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if proc.returncode != 0:
            return {
                "sampled_frames": 0,
                "unique_frames": 0,
                "error": proc.stderr.strip() or f"ffmpeg exited {proc.returncode}",
            }
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "sampled_frames": 0,
            "unique_frames": 0,
            "error": f"{type(exc).__name__}: {exc}",
        }
    signatures = []
    for line in proc.stdout.splitlines():
        if not line or line.startswith("#"):
            continue
        fields = [field.strip() for field in line.split(",")]
        if len(fields) >= 6 and fields[-1]:
            signatures.append(fields[-1])
    return {
        "sampled_frames": len(signatures),
        "unique_frames": len(set(signatures)),
        "error": "",
    }


def _normalize_primary_video(artifact_dir: Path) -> Path | None:
    primary = artifact_dir / "video.mp4"
    if primary.exists():
        return primary
    candidates = sorted(
        path for path in artifact_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in VIDEO_SUFFIXES and "source" not in path.parts
    )
    if not candidates:
        return None
    shutil.copy2(candidates[0], primary)
    return primary


def _make_poster(video: Path, poster: Path, metadata: dict[str, Any]) -> str:
    if poster.exists():
        return ""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return "ffmpeg not found"
    duration = float(metadata.get("duration_seconds") or 0.0)
    seek = max(0.0, min(duration / 2.0, 3.0))
    cmd = [
        ffmpeg, "-y", "-loglevel", "error", "-ss", f"{seek:.3f}",
        "-i", str(video), "-frames:v", "1", "-vf", "scale='min(1280,iw)':-2", str(poster),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        return "" if proc.returncode == 0 else (proc.stderr.strip() or f"ffmpeg exited {proc.returncode}")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"{type(exc).__name__}: {exc}"


def _has_any(artifact_dir: Path, names: list[str]) -> bool:
    return any((artifact_dir / name).exists() for name in names)


def _source_metadata(assets: list[dict[str, Any]]) -> dict[str, Any]:
    source_videos = _source_video_paths(assets)
    if not source_videos:
        return {}
    metadata = _probe_video(source_videos[0])
    metadata["path"] = str(source_videos[0])
    return metadata


def postprocess_candidate(
    candidate_dir: Path,
    video_type: str,
    assets: list[dict[str, Any]],
) -> dict[str, Any]:
    artifact_dir = candidate_dir / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    video = _normalize_primary_video(artifact_dir)
    metadata = _probe_video(video) if video else {}
    frame_variation = _sample_frame_variation(video) if video and not metadata.get("error") else {
        "sampled_frames": 0,
        "unique_frames": 0,
        "error": "video unavailable",
    }
    metadata.update({
        "sampled_frames": int(frame_variation.get("sampled_frames") or 0),
        "unique_frames": int(frame_variation.get("unique_frames") or 0),
    })
    poster_error = ""
    poster = artifact_dir / "poster.png"
    if video and not metadata.get("error"):
        poster_error = _make_poster(video, poster, metadata)

    source_dir = artifact_dir / "source"
    source_zip = artifact_dir / "source.zip"
    if source_dir.exists() and not source_zip.exists() and any(source_dir.rglob("*")):
        shutil.make_archive(str(source_zip.with_suffix("")), "zip", root_dir=source_dir)

    summary_exists = bool(_read_optional(candidate_dir / "summary.md") or _read_optional(artifact_dir / "summary.md"))
    source_meta = _source_metadata(assets)
    common = {
        "video_present": {
            "passed": bool(video and video.exists()),
            "detail": str(video or "video.mp4 missing"),
        },
        "video_decodes": {
            "passed": bool(metadata and not metadata.get("error") and metadata.get("duration_seconds", 0) > 0),
            "detail": metadata.get("error") or (
                f"{metadata.get('duration_seconds', 0):.2f}s, "
                f"{metadata.get('width', 0)}x{metadata.get('height', 0)}, "
                f"{metadata.get('video_codec') or 'unknown codec'}"
            ),
        },
        "dimensions_present": {
            "passed": bool(metadata.get("width") and metadata.get("height")),
            "detail": f"{metadata.get('width', 0)}x{metadata.get('height', 0)}",
        },
        "poster_present": {
            "passed": poster.exists(),
            "detail": str(poster) if poster.exists() else (poster_error or "poster.png missing"),
        },
        "summary_present": {
            "passed": summary_exists,
            "detail": "summary available" if summary_exists else "summary.md missing",
        },
    }

    if video_type == "talking_head_cut":
        source_duration = float(source_meta.get("duration_seconds") or 0.0)
        output_duration = float(metadata.get("duration_seconds") or 0.0)
        checks = {
            **common,
            "audio_preserved": {
                "passed": bool(metadata.get("has_audio")),
                "detail": metadata.get("audio_codec") or "audio track missing",
            },
            "duration_not_longer": {
                "passed": bool(source_duration and output_duration and output_duration <= source_duration + 0.25),
                "detail": (
                    f"source={source_duration:.2f}s, output={output_duration:.2f}s"
                    if source_duration else "source duration unavailable"
                ),
            },
            "transcript_present": {
                "passed": _has_any(artifact_dir, ["transcript.md", "transcript.txt"]),
                "detail": "transcript found" if _has_any(artifact_dir, ["transcript.md", "transcript.txt"]) else "transcript missing",
            },
            "subtitles_present": {
                "passed": _has_any(artifact_dir, ["subtitles.srt", "subtitles.vtt"]),
                "detail": "subtitle file found" if _has_any(artifact_dir, ["subtitles.srt", "subtitles.vtt"]) else "subtitle file missing",
            },
            "cut_list_present": {
                "passed": _has_any(artifact_dir, ["cut-list.json", "timeline.json", "edit.edl", "timeline.xml"]),
                "detail": "auditable edit decisions found" if _has_any(artifact_dir, ["cut-list.json", "timeline.json", "edit.edl", "timeline.xml"]) else "cut list or timeline missing",
            },
        }
        weights = {
            "video_present": 15, "video_decodes": 20, "dimensions_present": 5,
            "poster_present": 5, "summary_present": 5, "audio_preserved": 15,
            "duration_not_longer": 10, "transcript_present": 10,
            "subtitles_present": 5, "cut_list_present": 10,
        }
    elif video_type == "motion_graphics":
        checks = {
            **common,
            "minimum_duration": {
                "passed": float(metadata.get("duration_seconds") or 0.0) >= 3.0,
                "detail": f"{float(metadata.get('duration_seconds') or 0.0):.2f}s; minimum 3.00s",
            },
            "visual_variation": {
                "passed": int(frame_variation.get("unique_frames") or 0) >= 2,
                "detail": (
                    f"{frame_variation.get('unique_frames', 0)} unique frames from "
                    f"{frame_variation.get('sampled_frames', 0)} samples"
                    if not frame_variation.get("error") else str(frame_variation["error"])
                ),
            },
            "motion_spec_present": {
                "passed": _has_any(artifact_dir, ["motion-spec.md", "README.md"]),
                "detail": "motion specification found" if _has_any(artifact_dir, ["motion-spec.md", "README.md"]) else "motion-spec.md missing",
            },
            "source_delivered": {
                "passed": source_zip.exists() or (source_dir.exists() and any(source_dir.rglob("*"))),
                "detail": "editable source found" if source_zip.exists() or source_dir.exists() else "source package missing",
            },
        }
        weights = {
            "video_present": 15, "video_decodes": 20, "dimensions_present": 10,
            "poster_present": 10, "summary_present": 10,
            "minimum_duration": 5, "visual_variation": 5,
            "motion_spec_present": 10, "source_delivered": 15,
        }
    else:
        checks = {
            **common,
            "minimum_duration": {
                "passed": float(metadata.get("duration_seconds") or 0.0) >= 3.0,
                "detail": f"{float(metadata.get('duration_seconds') or 0.0):.2f}s; minimum 3.00s",
            },
            "visual_variation": {
                "passed": int(frame_variation.get("unique_frames") or 0) >= 2,
                "detail": (
                    f"{frame_variation.get('unique_frames', 0)} unique frames from "
                    f"{frame_variation.get('sampled_frames', 0)} samples"
                    if not frame_variation.get("error") else str(frame_variation["error"])
                ),
            },
            "audio_present": {
                "passed": bool(metadata.get("has_audio")),
                "detail": metadata.get("audio_codec") or "audio track missing",
            },
            "script_present": {
                "passed": (artifact_dir / "script.md").exists(),
                "detail": "script.md found" if (artifact_dir / "script.md").exists() else "script.md missing",
            },
            "storyboard_present": {
                "passed": (artifact_dir / "storyboard.md").exists(),
                "detail": "storyboard.md found" if (artifact_dir / "storyboard.md").exists() else "storyboard.md missing",
            },
            "subtitles_present": {
                "passed": _has_any(artifact_dir, ["subtitles.srt", "subtitles.vtt"]),
                "detail": "subtitle file found" if _has_any(artifact_dir, ["subtitles.srt", "subtitles.vtt"]) else "subtitle file missing",
            },
            "source_delivered": {
                "passed": source_zip.exists() or (source_dir.exists() and any(source_dir.rglob("*"))),
                "detail": "editable source found" if source_zip.exists() or source_dir.exists() else "source package missing",
            },
        }
        weights = {
            "video_present": 10, "video_decodes": 15, "dimensions_present": 5,
            "poster_present": 5, "summary_present": 5, "audio_present": 10,
            "minimum_duration": 5, "visual_variation": 10,
            "script_present": 10, "storyboard_present": 10,
            "subtitles_present": 5, "source_delivered": 10,
        }

    score = sum(weight for name, weight in weights.items() if checks[name]["passed"])
    qa = {
        "score": score,
        "video_type": video_type,
        "checks": checks,
        "output_metadata": metadata,
        "source_metadata": source_meta,
        "frame_variation": frame_variation,
        "poster_error": poster_error,
    }
    (artifact_dir / "qa.json").write_text(json.dumps(qa, ensure_ascii=False, indent=2), encoding="utf-8")
    return qa


def collect_candidate_artifacts(candidate_dir: Path, output_dir: Path) -> list[dict[str, Any]]:
    artifact_dir = candidate_dir / "artifacts"
    if not artifact_dir.exists():
        return []
    artifacts: list[dict[str, Any]] = []
    for path in sorted(path for path in artifact_dir.rglob("*") if path.is_file()):
        if path.name == ".gitkeep" or path.suffix.lower() not in ARTIFACT_SUFFIXES:
            continue
        relative_parts = path.relative_to(artifact_dir).parts
        if relative_parts[0] in {"source", "work"} and path.name != "source.zip":
            continue
        entry = {
            "path": _relative(path, output_dir),
            "label": _relative(path, artifact_dir),
            "kind": _kind_for(path),
        }
        if path.suffix.lower() in VIDEO_SUFFIXES | IMAGE_SUFFIXES | {".html", ".pdf"}:
            entry["preview_path"] = _relative(path, output_dir)
        artifacts.append(entry)
    return artifacts


def candidate_summary(pipeline: VideoPipeline, candidate_dir: Path) -> str:
    summary = _read_optional(candidate_dir / "summary.md") or _read_optional(candidate_dir / "artifacts" / "summary.md")
    runner = _read_optional(candidate_dir / "runner-output.md")
    return "\n\n".join(part for part in [pipeline.summary_zh, summary, runner] if part)


def estimate_candidate_tokens(
    task_input: str,
    pipeline: VideoPipeline,
    candidate_dir: Path,
    summary: str,
    artifacts: list[dict[str, Any]],
    run_result: dict[str, Any],
) -> int:
    sys.path.insert(0, str(SCRIPT_DIR))
    from compare import estimate_text_tokens

    prompt = _read_optional(candidate_dir / "RUN_PROMPT.md") or _read_optional(candidate_dir / "INSTRUCTIONS.md")
    artifact_text = "\n".join(
        f"{artifact.get('label') or ''} {artifact.get('kind') or ''}"
        for artifact in artifacts
    )
    visible = "\n\n".join(
        part for part in [prompt, task_input, str(run_result.get("output") or ""), summary, artifact_text] if part
    )
    return estimate_text_tokens(visible)


def _video_preview(candidate_dir: Path, output_dir: Path) -> dict[str, Any]:
    artifact_dir = candidate_dir / "artifacts"
    qa: dict[str, Any] = {}
    try:
        qa = json.loads((artifact_dir / "qa.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    preview: dict[str, Any] = {}
    video = artifact_dir / "video.mp4"
    poster = artifact_dir / "poster.png"
    if video.exists():
        preview["video_path"] = _relative(video, output_dir)
    if poster.exists():
        preview["poster_path"] = _relative(poster, output_dir)
    if qa or preview:
        preview["qa_score"] = int(qa.get("score") or 0)
        preview["metadata"] = qa.get("output_metadata") or {}
    return preview


def build_manifest(
    task_input: str,
    output_dir: Path,
    pipeline_ids: list[str],
    video_type: str,
    pipeline_registry: dict[str, VideoPipeline],
    assets: list[dict[str, Any]],
) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    for pipeline_id in pipeline_ids:
        pipeline = pipeline_registry[pipeline_id]
        candidate_dir = output_dir / "candidates" / pipeline.id
        run_result = _load_run_result(candidate_dir)
        artifacts = collect_candidate_artifacts(candidate_dir, output_dir)
        summary = candidate_summary(pipeline, candidate_dir)
        video_preview = _video_preview(candidate_dir, output_dir)
        reported_error = run_result.get("error")
        if (
            reported_error
            and "timeout" in str(reported_error).lower()
            and int(video_preview.get("qa_score") or 0) >= 90
        ):
            reported_error = None
        candidates.append({
            "id": pipeline.id,
            "name": pipeline.name,
            "author": pipeline.author,
            "category": f"video-artifact:{video_type}",
            "summary": summary,
            "workdir": _relative(candidate_dir, output_dir),
            "pipeline_steps": list(pipeline.pipeline_steps),
            "skill_source": pipeline.skill_source,
            "maturity": pipeline.maturity,
            "requires": list(pipeline.requires or []),
            "expected_artifacts": list(pipeline.expected_artifacts),
            "qa_checks": list(pipeline.qa_checks),
            "artifacts": artifacts,
            "video_preview": video_preview,
            "tokens_used": int(run_result.get("tokens_used") or 0),
            "provider_tokens_used": int(run_result.get("tokens_used") or 0),
            "estimated_tokens_used": estimate_candidate_tokens(
                task_input, pipeline, candidate_dir, summary, artifacts, run_result
            ),
            "latency_seconds": float(run_result.get("latency_seconds") or 0.0),
            "error": reported_error,
        })
    return {
        "schema_version": "video-artifact-v0.6",
        "deliverable_type": "video_artifact",
        "video_type": video_type,
        "task_input_path": "task.md",
        "assets": assets,
        "duration_seconds": max((candidate["latency_seconds"] for candidate in candidates), default=0.0),
        "artifact_contract": {
            "primary_video": "video.mp4",
            "shared_qa": "qa.json",
            "product_promo": ["poster.png", "subtitles.srt", "script.md", "storyboard.md", "source.zip"],
            "motion_graphics": ["poster.png", "motion-spec.md", "source.zip"],
            "talking_head_cut": ["subtitles.srt", "transcript.md", "cut-list.json", "timeline.json"],
        },
        "candidates": candidates,
    }


def build_artifact_judge_results(manifest: dict[str, Any], output_dir: Path) -> list[Any]:
    sys.path.insert(0, str(SCRIPT_DIR))
    from compare import RunResult

    results = []
    for candidate in manifest.get("candidates", []):
        preview = candidate.get("video_preview") or {}
        metadata = preview.get("metadata") or {}
        qa: dict[str, Any] = {}
        qa_path = output_dir / str(candidate.get("workdir") or "") / "artifacts" / "qa.json"
        try:
            qa = json.loads(qa_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
        checks = "\n".join(
            f"- {name}: {'pass' if detail.get('passed') else 'fail'}; {detail.get('detail') or ''}"
            for name, detail in (qa.get("checks") or {}).items()
            if isinstance(detail, dict)
        )
        artifacts = "\n".join(
            f"- {artifact.get('label')} ({artifact.get('kind')})"
            for artifact in candidate.get("artifacts") or []
        )
        summary = str(candidate.get("summary") or "")
        if len(summary) > 2400:
            summary = summary[:2400] + "\n[summary truncated for judge]"
        output = (
            f"{summary}\n\n## Shared media metadata\n"
            f"duration={metadata.get('duration_seconds', 0)}s; "
            f"dimensions={metadata.get('width', 0)}x{metadata.get('height', 0)}; "
            f"video_codec={metadata.get('video_codec', '')}; has_audio={metadata.get('has_audio', False)}\n\n"
            f"## Shared video QA\nScore: {preview.get('qa_score', 0)}/100\n"
            f"{checks or 'No QA checks available.'}\n\n"
            f"## Generated artifacts\n{artifacts or 'No generated artifacts.'}"
        )
        results.append(RunResult(
            skill_id=str(candidate.get("id")),
            skill_name=str(candidate.get("name")),
            skill_author=str(candidate.get("author") or ""),
            skill_category=str(candidate.get("category") or "video-artifact"),
            output=output,
            tokens_used=int(candidate.get("tokens_used") or 0),
            latency_seconds=float(candidate.get("latency_seconds") or 0.0),
            estimated_tokens_used=int(candidate.get("estimated_tokens_used") or 0),
            provider_tokens_used=int(candidate.get("provider_tokens_used") or 0),
            error=None if candidate.get("artifacts") else candidate.get("error"),
        ))
    return results


def _judge_rubric(video_type: str) -> str:
    if video_type == "talking_head_cut":
        return (
            "Evaluate talking-head rough cuts using only summaries, ffprobe metadata, artifact lists, and shared QA. "
            "Score semantic continuity 30%, correct removal of silence/repetition/mistakes 20%, preservation of useful content 20%, "
            "audio/video integrity 10%, subtitle/transcript/cut-list completeness 15%, and downstream editability 5%. "
            "Do not reward B-roll, music, generated scenes, visual redesign, or script rewriting in cut-only mode."
        )
    if video_type == "motion_graphics":
        return (
            "Evaluate finished motion-graphics candidates using summaries, media metadata, artifact lists, and shared QA. "
            "Score information fidelity 30%, motion timing and hierarchy 25%, typography/readability 20%, render integrity 10%, "
            "aspect-ratio and delivery completeness 10%, and editable source 5%. Visual style claims remain human-verifiable."
        )
    return (
        "Evaluate finished product-promo candidates using summaries, media metadata, artifact lists, and shared QA. "
        "Score product value clarity 25%, narrative and hook 20%, authentic product proof 15%, pacing and visual hierarchy 15%, "
        "audio/caption coherence 10%, brand fit 10%, and delivery completeness 5%. Visual claims remain human-verifiable."
    )


def run_artifact_judge(
    task_input: str,
    manifest: dict[str, Any],
    output_dir: Path,
    rubric: str | None = None,
    timeout: int = 120,
) -> dict[str, Any]:
    sys.path.insert(0, str(SCRIPT_DIR))
    from compare import run_judge

    effort_key = "FORKPROBE_CODEX_REASONING_EFFORT"
    previous_effort = os.environ.get(effort_key)
    os.environ[effort_key] = os.environ.get("FORKPROBE_VIDEO_JUDGE_REASONING_EFFORT", "low")
    try:
        with contextlib.redirect_stdout(sys.stderr):
            judge = run_judge(
                task_input,
                build_artifact_judge_results(manifest, output_dir),
                rubric=rubric or _judge_rubric(str(manifest.get("video_type") or "product_promo")),
                timeout=timeout,
            )
    finally:
        if previous_effort is None:
            os.environ.pop(effort_key, None)
        else:
            os.environ[effort_key] = previous_effort
    return asdict(judge)


def create_workspace(
    task_input: str,
    output_dir: Path,
    pipeline_ids: list[str] | None = None,
    skill_sources: list[str] | None = None,
    asset_paths: list[str] | None = None,
    max_candidates: int | None = None,
) -> dict[str, Any]:
    output_dir = output_dir.expanduser().resolve()
    video_type = detect_video_type(task_input)
    registry, dynamic_ids = build_pipeline_registry(skill_sources)
    selected_ids = list(pipeline_ids or default_pipeline_ids(video_type, max_candidates))
    selected_ids.extend(pipeline_id for pipeline_id in dynamic_ids if pipeline_id not in selected_ids)
    unknown = [pipeline_id for pipeline_id in selected_ids if pipeline_id not in registry]
    if unknown:
        raise KeyError(f"Unknown video pipeline(s): {', '.join(unknown)}")
    assets = _asset_records(asset_paths)

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "task.md").write_text(task_input, encoding="utf-8")
    (output_dir / "assets.json").write_text(json.dumps(assets, ensure_ascii=False, indent=2), encoding="utf-8")
    for pipeline_id in selected_ids:
        pipeline = registry[pipeline_id]
        candidate_dir = output_dir / "candidates" / pipeline.id
        artifact_dir = candidate_dir / "artifacts"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        (artifact_dir / ".gitkeep").write_text("", encoding="utf-8")
        (candidate_dir / "INSTRUCTIONS.md").write_text(
            build_pipeline_instructions(task_input, pipeline, candidate_dir, video_type, assets),
            encoding="utf-8",
        )
    manifest = build_manifest(task_input, output_dir, selected_ids, video_type, registry, assets)
    manifest_path = output_dir / "artifact-manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "output_dir": str(output_dir),
        "manifest_path": str(manifest_path),
        "video_type": video_type,
        "pipelines": selected_ids,
        "assets": assets,
        "skill_sources": list(skill_sources or []),
        "manifest": manifest,
    }


def _codex_cli_path() -> str | None:
    for candidate in [
        os.environ.get("FORKPROBE_CODEX_CLI"),
        os.environ.get("CODEX_CLI_PATH"),
        shutil.which("codex"),
        "/Applications/Codex.app/Contents/Resources/codex",
    ]:
        if candidate and Path(candidate).exists():
            return candidate
    return None


def _parse_codex_tokens(text: str) -> int:
    match = re.search(r"tokens used\s+([0-9][0-9,]*)", text, flags=re.IGNORECASE)
    return int(match.group(1).replace(",", "")) if match else 0


def _tail(text: str, limit: int = 1400) -> str:
    text = text.strip()
    return text if len(text) <= limit else "..." + text[-limit:]


def _has_generated_artifacts(candidate_dir: Path) -> bool:
    artifact_dir = candidate_dir / "artifacts"
    return artifact_dir.exists() and any(
        path.is_file() and path.name != ".gitkeep"
        for path in artifact_dir.rglob("*")
    )


def _write_run_result(candidate_dir: Path, result: VideoRunResult) -> None:
    (candidate_dir / "run-result.json").write_text(
        json.dumps(asdict(result), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if result.output:
        (candidate_dir / "runner-output.md").write_text(result.output, encoding="utf-8")
    if result.error:
        (candidate_dir / "runner-error.txt").write_text(result.error, encoding="utf-8")


def run_candidate_codex(
    task_input: str,
    output_dir: Path,
    pipeline_id: str,
    video_type: str,
    assets: list[dict[str, Any]],
    timeout: int = 900,
    pipeline_registry: dict[str, VideoPipeline] | None = None,
) -> VideoRunResult:
    registry = pipeline_registry or VIDEO_PIPELINES
    if pipeline_id not in registry:
        raise KeyError(f"Unknown video pipeline: {pipeline_id}")
    pipeline = registry[pipeline_id]
    candidate_dir = output_dir / "candidates" / pipeline.id
    prompt = build_candidate_run_prompt(task_input, pipeline, candidate_dir, video_type, assets)
    (candidate_dir / "RUN_PROMPT.md").write_text(prompt, encoding="utf-8")
    cli = _codex_cli_path()
    if not cli:
        result = VideoRunResult(pipeline.id, "", 0, 0.0, "Codex CLI not found. Set FORKPROBE_CODEX_CLI or install Codex CLI.")
        _write_run_result(candidate_dir, result)
        return result

    sandbox = os.environ.get("FORKPROBE_VIDEO_SANDBOX", "workspace-write")
    model = os.environ.get("FORKPROBE_MODEL_CODEX_NATIVE")
    reasoning_effort = os.environ.get("FORKPROBE_CODEX_REASONING_EFFORT")
    t0 = time.time()
    output_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(prefix="forkprobe-video-codex-", suffix=".txt", delete=False) as handle:
            output_path = Path(handle.name)
        cmd = [
            cli, "exec", "--ephemeral", "--skip-git-repo-check", "--sandbox", sandbox,
            "--output-last-message", str(output_path), "-C", str(PROJECT_DIR),
            "--add-dir", str(output_dir),
        ]
        for asset in assets:
            path = Path(str(asset.get("path") or ""))
            add_dir = path if path.is_dir() else path.parent
            if add_dir.exists():
                cmd.extend(["--add-dir", str(add_dir)])
        if model:
            cmd.extend(["--model", model])
        if reasoning_effort:
            cmd.extend(["-c", f'model_reasoning_effort="{reasoning_effort}"'])
        cmd.append("-")
        proc = subprocess.run(cmd, input=prompt, text=True, capture_output=True, timeout=timeout)
        transcript = f"{proc.stdout}\n{proc.stderr}"
        output = output_path.read_text(encoding="utf-8").strip() if output_path.exists() else ""
        output = output or proc.stdout.strip()
        error = None if proc.returncode == 0 else f"Codex CLI exited {proc.returncode}: {_tail(transcript)}"
        result = VideoRunResult(pipeline.id, output, _parse_codex_tokens(transcript), time.time() - t0, error)
    except subprocess.TimeoutExpired:
        partial = " Partial artifacts are available." if _has_generated_artifacts(candidate_dir) else ""
        result = VideoRunResult(pipeline.id, "", 0, time.time() - t0, f"Codex CLI timeout after {timeout}s.{partial}")
    except Exception as exc:
        result = VideoRunResult(pipeline.id, "", 0, time.time() - t0, f"{type(exc).__name__}: {exc}")
    finally:
        if output_path:
            try:
                output_path.unlink(missing_ok=True)
            except OSError:
                pass
    _write_run_result(candidate_dir, result)
    postprocess_candidate(candidate_dir, video_type, assets)
    return result


def run_parallel(
    task_input: str,
    output_dir: Path,
    pipeline_ids: list[str],
    video_type: str,
    assets: list[dict[str, Any]],
    pipeline_registry: dict[str, VideoPipeline] | None = None,
    max_workers: int = 2,
    timeout: int = 900,
) -> list[VideoRunResult]:
    registry = pipeline_registry or VIDEO_PIPELINES
    max_workers = int(os.environ.get("FORKPROBE_VIDEO_MAX_WORKERS", str(max_workers)))
    results: list[VideoRunResult] = []
    with ThreadPoolExecutor(max_workers=min(max_workers, len(pipeline_ids))) as executor:
        futures = {
            executor.submit(
                run_candidate_codex, task_input, output_dir, pipeline_id,
                video_type, assets, timeout, registry,
            ): pipeline_id
            for pipeline_id in pipeline_ids
        }
        for future in as_completed(futures):
            pipeline_id = futures[future]
            candidate_dir = output_dir / "candidates" / pipeline_id
            try:
                result = future.result()
            except Exception as exc:
                result = VideoRunResult(pipeline_id, "", 0, 0.0, f"{type(exc).__name__}: {exc}")
                _write_run_result(candidate_dir, result)
            status = "ok" if not result.error else ("partial" if _has_generated_artifacts(candidate_dir) else "error")
            print(f"[forkprobe] video pipeline {pipeline_id}: {status} ({result.latency_seconds:.1f}s)", file=sys.stderr)
            results.append(result)
    order = {pipeline_id: index for index, pipeline_id in enumerate(pipeline_ids)}
    results.sort(key=lambda result: order.get(result.pipeline_id, 999))
    return results


def _read_task(args: argparse.Namespace) -> str:
    if args.input:
        return Path(args.input).expanduser().read_text(encoding="utf-8")
    if args.text:
        return args.text
    return sys.stdin.read()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run finished-video artifact comparisons")
    parser.add_argument("--input", help="Path to task input text")
    parser.add_argument("--text", help="Task description text when --input is omitted")
    parser.add_argument("--asset", action="append", default=[], help="Shared source asset file or directory; repeat for multiple")
    parser.add_argument("--output-dir", help="Workspace directory; defaults to outputs/video-runs/<timestamp>")
    parser.add_argument("--pipeline", action="append", default=[], help="Video pipeline id; repeat to override defaults")
    parser.add_argument("--skill-source", action="append", default=[], help="External video skill source; repeat for multiple")
    parser.add_argument("--max-candidates", type=int, default=None, help="Maximum default candidates; defaults to the full scene shortlist")
    parser.add_argument("--run", action="store_true", help="Run candidates in parallel")
    parser.add_argument("--refresh-artifacts", action="store_true", help="Re-run ffprobe, poster generation, packaging, and shared QA")
    parser.add_argument("--confirmed", action="store_true", help="Acknowledge that the user confirmed the shortlist")
    parser.add_argument("--timeout", type=int, default=900, help="Seconds allowed for each candidate (default: 900)")
    parser.add_argument("--max-workers", type=int, default=2, help="Maximum concurrent candidate runs")
    parser.add_argument("--judge", action="store_true", help="Run AI judging after generation and media QA")
    parser.add_argument("--judge-rubric", default=None, help="Optional extra AI judge rubric")
    parser.add_argument("--judge-timeout", type=int, default=120, help="Seconds allowed for AI judge")
    parser.add_argument("--render-report", action="store_true", help="Render the artifact comparison report")
    parser.add_argument("--report-output", default="video-artifact-report.html", help="Report path inside workspace or absolute path")
    parser.add_argument("--no-open", action="store_true", help="Do not auto-open the report")
    parser.add_argument("--json", action="store_true", help="Print JSON summary")
    args = parser.parse_args()

    task_input = _read_task(args)
    if not task_input.strip():
        raise SystemExit("Task input is empty.")
    if args.run and not args.confirmed:
        raise SystemExit(
            "Refusing to run video pipelines before candidate confirmation. First run "
            "`python3 scripts/recommend.py --input <input.txt>`, show the shortlist, then rerun with --confirmed."
        )
    video_type = detect_video_type(task_input)
    assets = _asset_records(args.asset)
    if args.run and video_type == "talking_head_cut" and not _source_video_paths(assets):
        raise SystemExit("Talking-head rough cut requires at least one existing source video via --asset <video>.")

    timestamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    output_dir = Path(args.output_dir).expanduser() if args.output_dir else DEFAULT_OUTPUT_ROOT / f"{timestamp}-{_slugify(task_input[:48])}"
    result = create_workspace(
        task_input, output_dir, args.pipeline or None, args.skill_source,
        args.asset, args.max_candidates,
    )
    registry, _ = build_pipeline_registry(args.skill_source)
    if args.run:
        run_parallel(
            task_input, Path(result["output_dir"]), list(result["pipelines"]),
            result["video_type"], result["assets"], registry, args.max_workers, args.timeout,
        )
        result = create_workspace(
            task_input, Path(result["output_dir"]), list(result["pipelines"]),
            args.skill_source, args.asset, args.max_candidates,
        )
    elif args.refresh_artifacts:
        for pipeline_id in result["pipelines"]:
            postprocess_candidate(
                Path(result["output_dir"]) / "candidates" / pipeline_id,
                result["video_type"], result["assets"],
            )
        result = create_workspace(
            task_input, Path(result["output_dir"]), list(result["pipelines"]),
            args.skill_source, args.asset, args.max_candidates,
        )

    if args.judge:
        manifest_path = Path(result["manifest_path"])
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["judge"] = run_artifact_judge(
            task_input, manifest, Path(result["output_dir"]), args.judge_rubric, args.judge_timeout,
        )
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        result["manifest"] = manifest

    if args.render_report or args.run:
        sys.path.insert(0, str(SCRIPT_DIR))
        from render_artifact_report import render_from_manifest

        report_path = Path(args.report_output)
        if not report_path.is_absolute():
            report_path = Path(result["output_dir"]) / report_path
        render_from_manifest(Path(result["manifest_path"]), report_path, auto_open=not args.no_open)
        result["report_path"] = str(report_path.resolve())

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"[forkprobe] Video workspace: {result['output_dir']}")
        print(f"[forkprobe] Video type: {result['video_type']}")
        print(f"[forkprobe] Pipelines: {', '.join(result['pipelines'])}")
        print(f"[forkprobe] Manifest: {result['manifest_path']}")
        if result.get("report_path"):
            print(f"[forkprobe] Report: {result['report_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
