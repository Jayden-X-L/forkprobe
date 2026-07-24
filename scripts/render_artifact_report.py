"""
Render a forkprobe artifact comparison report.

This helper is for file-producing workflows such as PPTX and video comparison. It does
not generate artifacts itself; the active agent creates one artifact per
pipeline, writes a manifest, then this script renders a report with file links,
optional previews, judge notes, and winner selection.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))

from render_report import render


def _read_task_input(manifest: dict[str, Any], manifest_dir: Path) -> str:
    if manifest.get("task_input"):
        return str(manifest["task_input"])
    if manifest.get("task_input_path"):
        path = _resolve_path(str(manifest["task_input_path"]), manifest_dir)
        return path.read_text(encoding="utf-8")
    return ""


def _resolve_path(value: str, base_dir: Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    return path


def _href_for_path(value: str, base_dir: Path) -> str:
    path = _resolve_path(value, base_dir)
    try:
        return path.resolve().as_uri()
    except ValueError:
        return value


def _preview_kind(value: str) -> str:
    suffix = Path(value.split("?", 1)[0].split("#", 1)[0]).suffix.lower()
    if suffix in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"}:
        return "image"
    if suffix in {".mp4", ".webm", ".mov", ".m4v"}:
        return "video"
    if suffix in {".mp3", ".wav", ".m4a", ".aac", ".ogg"}:
        return "audio"
    if suffix in {".html", ".htm"}:
        return "html"
    if suffix == ".pdf":
        return "pdf"
    return "file"


def _normalize_artifact(artifact: dict[str, Any], manifest_dir: Path) -> dict[str, Any]:
    path_value = str(artifact.get("path") or artifact.get("href") or "")
    preview_value = str(artifact.get("preview_path") or artifact.get("preview_href") or "")
    normalized = {
        "label": artifact.get("label") or (Path(path_value).name if path_value else "artifact"),
        "kind": artifact.get("kind") or (Path(path_value).suffix.lstrip(".").upper() if path_value else "file"),
        "path": path_value,
        "href": artifact.get("href") or (_href_for_path(path_value, manifest_dir) if path_value else ""),
    }
    if preview_value:
        normalized["preview_path"] = preview_value
        normalized["preview_href"] = artifact.get("preview_href") or _href_for_path(preview_value, manifest_dir)
        normalized["preview_kind"] = artifact.get("preview_kind") or _preview_kind(preview_value)
    return normalized


def _normalize_candidate(candidate: dict[str, Any], manifest_dir: Path) -> dict[str, Any]:
    candidate_id = str(candidate.get("id") or candidate.get("skill_id") or candidate.get("name") or "candidate")
    artifacts = [
        _normalize_artifact(artifact, manifest_dir)
        for artifact in candidate.get("artifacts", [])
    ]
    summary = candidate.get("summary") or candidate.get("output") or candidate.get("notes") or ""
    web_preview_source = candidate.get("web_preview") if isinstance(candidate.get("web_preview"), dict) else {}
    web_preview: dict[str, Any] = {}
    for source_key, target_key in (
        ("page_path", "page_href"),
        ("desktop_path", "desktop_href"),
        ("mobile_path", "mobile_href"),
    ):
        value = str(web_preview_source.get(source_key) or web_preview_source.get(target_key) or "")
        if value:
            web_preview[target_key] = _href_for_path(value, manifest_dir)
    if web_preview_source:
        web_preview["qa_score"] = int(web_preview_source.get("qa_score") or 0)
    video_preview_source = candidate.get("video_preview") if isinstance(candidate.get("video_preview"), dict) else {}
    video_preview: dict[str, Any] = {}
    for source_key, target_key in (
        ("video_path", "video_href"),
        ("poster_path", "poster_href"),
    ):
        value = str(video_preview_source.get(source_key) or video_preview_source.get(target_key) or "")
        if value:
            video_preview[target_key] = _href_for_path(value, manifest_dir)
    if video_preview_source:
        video_preview["qa_score"] = int(video_preview_source.get("qa_score") or 0)
        metadata = video_preview_source.get("metadata")
        video_preview["metadata"] = metadata if isinstance(metadata, dict) else {}
    return {
        "skill_id": candidate_id,
        "skill_name": candidate.get("name") or candidate.get("skill_name") or candidate_id,
        "skill_author": candidate.get("author") or candidate.get("skill_author") or "",
        "skill_category": candidate.get("category") or candidate.get("skill_category") or "artifact",
        "output": str(summary),
        "tokens_used": int(candidate.get("tokens_used") or 0),
        "provider_tokens_used": int(candidate.get("provider_tokens_used") or candidate.get("tokens_used") or 0),
        "estimated_tokens_used": int(candidate.get("estimated_tokens_used") or 0),
        "latency_seconds": float(candidate.get("latency_seconds") or 0.0),
        "error": candidate.get("error"),
        "artifacts": artifacts,
        "web_preview": web_preview,
        "video_preview": video_preview,
    }


def render_from_manifest(manifest_path: Path, output_path: Path, auto_open: bool = True) -> Path:
    manifest_path = manifest_path.expanduser().resolve()
    manifest_dir = manifest_path.parent
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    results = [
        _normalize_candidate(candidate, manifest_dir)
        for candidate in manifest.get("candidates", [])
    ]
    if not results:
        raise ValueError("Artifact manifest must contain at least one candidate.")

    return render(
        task_input=_read_task_input(manifest, manifest_dir),
        results=results,
        duration_seconds=float(manifest.get("duration_seconds") or 0.0),
        output_path=output_path,
        auto_open=auto_open,
        verdict_url=manifest.get("verdict_url") or "",
        judge_result=manifest.get("judge"),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Render a forkprobe artifact comparison report")
    parser.add_argument("--manifest", required=True, help="Path to artifact comparison manifest JSON")
    parser.add_argument("--output", default="./artifact-report.html", help="Output HTML path")
    parser.add_argument("--no-open", action="store_true", help="Do not open the report in a browser")
    args = parser.parse_args()

    output = render_from_manifest(
        manifest_path=Path(args.manifest),
        output_path=Path(args.output),
        auto_open=not args.no_open,
    )
    print(f"[forkprobe] Artifact report: {output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
