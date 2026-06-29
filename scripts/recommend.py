"""
forkprobe skill recommendation helper.

This is a lightweight preflight step. It turns a user's task description into a
small candidate set for compare.py. By default it combines local curated
candidates with GitHub/network discovery using sanitized task signals; use
--local-only when the user explicitly asks to stay local. It never decides the
winner and never calls a model.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional


SCRIPT_DIR = Path(__file__).parent
PROJECT_DIR = SCRIPT_DIR.parent
CATALOG_DIR = PROJECT_DIR / "catalog"
NATURE_SKILLS_REPO = "https://github.com/Yuan1z0825/nature-skills"

# Sibling import works when scripts/ is on sys.path (normal compare/recommend usage).
try:
    from discover_skills import discover as discover_skill_pipelines
    from discover_skills import discover_online_skills
except ImportError:  # pragma: no cover - direct import fallback for unusual launchers
    discover_skill_pipelines = None
    discover_online_skills = None


@dataclass
class RecommendedSkill:
    id: str
    name: str
    author: str
    kind: str
    command_arg: str
    reason_zh: str
    reason_en: str
    source: str = ""
    runnable: bool = True
    produces: str = "text"
    pipeline_steps: list[str] = field(default_factory=list)
    caution_zh: str = ""
    caution_en: str = ""
    source_kind: str = "local"
    score: int = 0
    stars: int = 0


@dataclass
class Recommendation:
    deliverable_type: str
    compare_mode: str
    task_signals: list[str]
    candidates: list[RecommendedSkill]
    notes_zh: list[str]
    notes_en: list[str]
    suggested_command: list[str]
    mode_explanation_zh: str = ""
    mode_explanation_en: str = ""
    discovery_queries: list[str] = field(default_factory=list)


CATALOG_COPY = {
    "baseline": {
        "name": "Baseline (no skill)",
        "author": "",
        "kind": "baseline",
        "reason_zh": "原始模型输出，作为参照。",
        "reason_en": "Raw model output, used as the reference.",
    },
    "humanizer": {
        "reason_zh": "适合英文文本的 anti-AI/humanize 对比。",
        "reason_en": "Useful for English anti-AI or humanized writing comparisons.",
    },
    "writing-anti-ai": {
        "reason_zh": "适合降低机器感，让中英文表达更自然。",
        "reason_en": "Useful for reducing AI-like phrasing in Chinese or English.",
    },
    "research-paper-writing-skills": {
        "reason_zh": "适合中文科研表达、论文段落和 SCI 写作语气优化。",
        "reason_en": "Useful for Chinese academic expression and SCI-style paper prose.",
    },
    "paper-writer-skill": {
        "reason_zh": "适合正式论文语气、IMRAD 结构和审稿回复类任务。",
        "reason_en": "Useful for formal manuscript tone, IMRAD structure, and reviewer-response tasks.",
    },
}


BYO_COPY = {
    "nature-polishing": RecommendedSkill(
        id="byo:nature-polishing",
        name="nature-polishing",
        author="Yuan1z",
        kind="byo",
        command_arg=f"{NATURE_SKILLS_REPO}#skills/nature-polishing",
        reason_zh="适合英文/Nature 风格润色、中译英和英文摘要优化。",
        reason_en="Useful for Nature-style English polishing, translation, and abstract refinement.",
    ),
    "nature-response": RecommendedSkill(
        id="byo:nature-response",
        name="nature-response",
        author="Yuan1z",
        kind="byo",
        command_arg=f"{NATURE_SKILLS_REPO}#skills/nature-response",
        reason_zh="适合返修、审稿人意见回复和 response letter。",
        reason_en="Useful for revision responses, reviewer comments, and response letters.",
    ),
    "nature-figure": RecommendedSkill(
        id="byo:nature-figure",
        name="nature-figure",
        author="Yuan1z",
        kind="byo",
        command_arg=f"{NATURE_SKILLS_REPO}#skills/nature-figure",
        reason_zh="适合 Nature 风格科研图、figure storyline、panel 结构和图注构思。",
        reason_en="Useful for Nature-style figures, figure storyline, panel structure, and caption planning.",
        caution_zh="如果最终要科研图成品，请走 figure artifact pipeline；这里仅用于明确只要图注/说明文字的任务。",
        caution_en="Use the figure artifact pipeline for finished scientific figures; keep this only for caption or planning-only tasks.",
    ),
    "nature-paper2ppt": RecommendedSkill(
        id="byo:nature-paper2ppt",
        name="nature-paper2ppt",
        author="Yuan1z",
        kind="byo",
        command_arg=f"{NATURE_SKILLS_REPO}#skills/nature-paper2ppt",
        reason_zh="适合把论文内容转成 Nature 风格汇报结构或 PPT 草案。",
        reason_en="Useful for turning paper content into a Nature-style presentation outline or draft.",
        caution_zh="这里用于 PPT 方案/大纲对比；如果要比较 PPTX 成品，请走 artifact 模式。",
        caution_en="Use this for PPT plan/outline comparison; use artifact mode to compare finished PPTX files.",
    ),
}


ARTIFACT_PIPELINES = {
    "baseline-presentations": RecommendedSkill(
        id="baseline-presentations",
        name="baseline + presentations",
        author="",
        kind="pipeline",
        command_arg="baseline+presentations",
        reason_zh="不使用专门规划 skill，直接用主模型和 Presentations 生成 PPTX，作为成品基线。",
        reason_en="Uses the main model plus Presentations directly as the artifact baseline.",
        runnable=False,
        produces="pptx",
        pipeline_steps=["baseline", "presentations:Presentations"],
    ),
    "nature-paper2ppt-presentations": RecommendedSkill(
        id="nature-paper2ppt-presentations",
        name="nature-paper2ppt + presentations",
        author="Yuan1z",
        kind="pipeline",
        command_arg=f"{NATURE_SKILLS_REPO}#skills/nature-paper2ppt+presentations",
        reason_zh="先用 Nature 风格论文转汇报 skill 做结构规划，再用 Presentations 生成 PPTX。",
        reason_en="Plans the deck with nature-paper2ppt, then generates the PPTX with Presentations.",
        runnable=False,
        produces="pptx",
        pipeline_steps=[f"{NATURE_SKILLS_REPO}#skills/nature-paper2ppt", "presentations:Presentations"],
    ),
    "pptx-direct": RecommendedSkill(
        id="pptx-direct",
        name="pptx",
        author="",
        kind="pipeline",
        command_arg="pptx",
        reason_zh="直接使用 PowerPoint 文件结构和版式控制，适合比较可编辑 PPTX 成品质量。",
        reason_en="Directly controls PowerPoint file structure and layout for editable PPTX quality.",
        runnable=False,
        produces="pptx",
        pipeline_steps=["pptx"],
    ),
    "storyboard-presentations": RecommendedSkill(
        id="storyboard-presentations",
        name="storyboard + presentations",
        author="",
        kind="pipeline",
        command_arg="storyboard+presentations",
        reason_zh="先梳理叙事流、页面节奏和视觉表达，再用 Presentations 生成 PPTX。",
        reason_en="Builds narrative flow and slide rhythm first, then generates the PPTX with Presentations.",
        runnable=False,
        produces="pptx",
        pipeline_steps=["storyboard", "presentations:Presentations"],
    ),
}


FIGURE_ARTIFACT_PIPELINES = {
    "baseline-python-figure": RecommendedSkill(
        id="baseline-python-figure",
        name="baseline + Python figure package",
        author="",
        kind="pipeline",
        command_arg="baseline-python-figure",
        reason_zh="不使用专门科研作图 skill，直接生成可复现的 Python/SVG 图包，作为成品基线。",
        reason_en="No specialized figure skill; produces a reproducible Python/SVG figure package as the baseline.",
        runnable=False,
        produces="figure_package",
        pipeline_steps=["baseline", "python/matplotlib-or-svg", "artifact-qa"],
        score=82,
    ),
    "nature-figure-python": RecommendedSkill(
        id="nature-figure-python",
        name="nature-figure + Python/SVG renderer",
        author="Yuan1z",
        kind="pipeline",
        command_arg=f"{NATURE_SKILLS_REPO}#skills/nature-figure+python-svg-renderer",
        reason_zh="先用 nature-figure 做科学设计、storyline、panel 结构和图注，再生成投稿级图包。",
        reason_en="Uses nature-figure for scientific design, storyline, panel structure, and caption before rendering a submission-oriented package.",
        runnable=False,
        produces="figure_package",
        pipeline_steps=[f"{NATURE_SKILLS_REPO}#skills/nature-figure", "python/svg-renderer", "artifact-qa"],
        caution_zh="这是科研图成品 pipeline，执行时应输出 PNG 预览、SVG/PDF/TIFF、源代码或矢量源文件、caption 和 QA。",
        caution_en="This is a scientific figure artifact pipeline; execution should output PNG preview, SVG/PDF/TIFF, source code or vector source, caption, and QA notes.",
        source_kind="known_github",
        score=88,
    ),
    "plot-code-python": RecommendedSkill(
        id="plot-code-python",
        name="data plot code pipeline",
        author="",
        kind="pipeline",
        command_arg="plot-code-python",
        reason_zh="面向真实数据作图：读取数据、生成绘图代码、导出 PNG/SVG/PDF/TIFF 和简短图注。",
        reason_en="For real data plots: load data, generate plotting code, export PNG/SVG/PDF/TIFF, and write a short caption.",
        runnable=False,
        produces="figure_package",
        pipeline_steps=["data-understanding", "python/matplotlib-or-seaborn", "export", "artifact-qa"],
        score=85,
    ),
    "schematic-svg": RecommendedSkill(
        id="schematic-svg",
        name="schematic SVG / draw.io pipeline",
        author="",
        kind="pipeline",
        command_arg="schematic-svg",
        reason_zh="面向机制图、架构图和流程图：先设计布局，再生成 SVG/draw.io 友好的矢量图包。",
        reason_en="For mechanism, architecture, and workflow diagrams: design layout first, then produce an SVG/draw.io-friendly vector package.",
        runnable=False,
        produces="figure_package",
        pipeline_steps=["brief-to-layout", "svg-or-drawio", "export", "artifact-qa"],
        score=84,
    ),
    "graphical-abstract-svg": RecommendedSkill(
        id="graphical-abstract-svg",
        name="graphical abstract SVG pipeline",
        author="",
        kind="pipeline",
        command_arg="graphical-abstract-svg",
        reason_zh="面向 graphical abstract：把论文 brief 转成单幅摘要图、导出预览和矢量源文件。",
        reason_en="For graphical abstracts: turn a paper brief into a single visual abstract with preview and vector source files.",
        runnable=False,
        produces="figure_package",
        pipeline_steps=["paper-brief", "visual-storyboard", "svg-render", "artifact-qa"],
        score=80,
    ),
}


def _pipeline_from_discovery(pipeline) -> RecommendedSkill:
    """Convert discover_skills.PipelineCandidate into recommend.py's UI model."""
    return RecommendedSkill(
        id=pipeline.id,
        name=pipeline.name,
        author="",
        kind="pipeline",
        command_arg=pipeline.id,
        reason_zh=pipeline.summary_zh,
        reason_en=pipeline.summary_en,
        source=pipeline.source,
        runnable=False,
        produces="pptx",
        pipeline_steps=list(pipeline.components),
        caution_zh=(
            f"状态: {pipeline.executable_status}。{pipeline.risk_zh}"
            if pipeline.risk_zh else f"状态: {pipeline.executable_status}。"
        ),
        caution_en=(
            f"Status: {pipeline.executable_status}. {pipeline.risk_en}"
            if pipeline.risk_en else f"Status: {pipeline.executable_status}."
        ),
        source_kind="local_or_curated_external",
        score=80 if pipeline.executable_status == "ready_or_local" else 70,
    )


def _skill_from_online_discovery(candidate, deliverable_type: str) -> RecommendedSkill:
    if deliverable_type == "pptx":
        produces = "pptx"
    elif deliverable_type == "visual_artifact":
        produces = "figure_package"
    else:
        produces = "text"
    return RecommendedSkill(
        id=candidate.id,
        name=candidate.name,
        author="GitHub",
        kind="github_discovered",
        command_arg=candidate.command_arg,
        reason_zh=candidate.summary_zh,
        reason_en=candidate.summary_en,
        source=candidate.source,
        runnable=bool(candidate.runnable and deliverable_type not in {"pptx", "visual_artifact"}),
        produces=produces,
        pipeline_steps=[candidate.command_arg] if deliverable_type in {"pptx", "visual_artifact"} else [],
        caution_zh=candidate.risk_zh,
        caution_en=candidate.risk_en,
        source_kind=candidate.category or "github_discovered",
        score=int(candidate.score),
        stars=int(candidate.stars),
    )


KEYWORDS = {
    "anti_ai": [
        "ai味", "ai 味", "机器感", "模板感", "不自然", "更自然", "降低ai", "降低 ai",
        "anti-ai", "ai-like", "humanize", "humanizer", "less ai",
    ],
    "english": [
        "英文", "英语", "中译英", "英译", "abstract", "english", "translate", "translation",
        "polish", "nature", "science", "cell",
    ],
    "nature": ["nature", "自然子刊", "nature 风格", "nature风格"],
    "chinese_academic": [
        "中文", "科研", "论文", "sci", "学术", "摘要", "方法", "结果", "讨论", "医学",
        "临床", "投稿", "润色",
    ],
    "rebuttal": [
        "rebuttal", "response letter", "reviewer", "revision", "审稿", "审稿人", "返修",
        "回复审稿", "大修", "小修",
    ],
    "figure": [
        "figure", "fig.", "图", "示意图", "画图", "作图", "绘图", "流程图", "图表",
        "机制图", "架构图", "graphical abstract", "schematic", "diagram",
        "plot", "graph", "visualization", "可视化",
    ],
    "slides": ["ppt", "slide", "slides", "汇报", "presentation", "deck", "答辩"],
}


TEXT_ONLY_HINTS = [
    "不要生成pptx", "不要生成 pptx", "不生成pptx", "不生成 pptx", "不要生成文件",
    "只要方案", "只给方案", "先给方案", "ppt方案", "ppt 方案", "ppt大纲", "ppt 大纲",
    "推荐页数", "每页标题", "核心要点", "讲述逻辑", "建议图表", "输出格式",
]

PPT_ARTIFACT_HINTS = [
    "做一个ppt", "做ppt", "做成ppt", "生成ppt", "正式ppt", "pptx", "powerpoint",
    "slide deck", "deck", "生成 slide", "生成slide",
]

FIGURE_TEXT_ONLY_HINTS = [
    "只要图注", "只给图注", "只要caption", "只给caption", "只要说明", "只给说明",
    "只要storyline", "只给storyline", "只看storyline", "不要生成图片", "不生成图片",
    "不要生成图", "不生成图", "不要生成文件", "只要代码草案", "只给代码草案",
]

FIGURE_ARTIFACT_HINTS = [
    "成品", "投稿", "最终图", "最终figure", "生成图片", "生成图", "生成示意图",
    "画图", "作图", "绘图", "png", "svg", "pdf", "tiff", "draw.io", "drawio",
    "源文件", "矢量", "可编辑", "figure package", "artifact",
]

LOCAL_ONLY_HINTS = [
    "只要本地", "仅本地", "只用本地", "本地候选", "不要联网", "别联网",
    "不联网", "离线", "local only", "offline", "no network",
]


def load_catalog(domain: str = "academic-writing") -> dict:
    catalog_path = CATALOG_DIR / f"{domain}.json"
    if not catalog_path.exists():
        raise FileNotFoundError(f"Catalog not found: {catalog_path}")
    return json.loads(catalog_path.read_text(encoding="utf-8"))


def _has_any(text: str, words: list[str]) -> bool:
    lower = text.lower()
    return any(word.lower() in lower for word in words)


def _compact(text: str) -> str:
    return "".join(text.lower().split())


def _has_compact_any(text: str, phrases: list[str]) -> bool:
    compact = _compact(text)
    return any(_compact(phrase) in compact for phrase in phrases)


def detect_task_signals(task_text: str) -> list[str]:
    signals = [name for name, words in KEYWORDS.items() if _has_any(task_text, words)]
    cjk_chars = sum(1 for ch in task_text if "\u4e00" <= ch <= "\u9fff")
    if cjk_chars >= 8 and "zh" not in signals:
        signals.append("zh")
    if not signals:
        signals.append("general")
    return signals


def detect_deliverable_type(task_text: str, signals: Optional[list[str]] = None) -> str:
    """Classify the requested output so artifact tasks do not get routed as text-only comparisons."""
    signals = signals or detect_task_signals(task_text)
    signal_set = set(signals)
    if "slides" in signal_set:
        if _has_compact_any(task_text, TEXT_ONLY_HINTS):
            return "ppt_outline"
        if _has_compact_any(task_text, PPT_ARTIFACT_HINTS):
            return "pptx"
        # In natural Chinese, "做一个 PPT" usually means a PPT file, not just an outline.
        if "ppt" in task_text.lower():
            return "pptx"
        return "ppt_outline"
    if "figure" in signal_set:
        if _has_compact_any(task_text, FIGURE_TEXT_ONLY_HINTS):
            return "text"
        return "visual_artifact"
    return "text"


def wants_local_only(task_text: str) -> bool:
    return _has_compact_any(task_text, LOCAL_ONLY_HINTS)


def _catalog_skill(skill_id: str, catalog: dict, reason_override: Optional[str] = None) -> RecommendedSkill:
    if skill_id == "baseline":
        meta = CATALOG_COPY["baseline"]
        return RecommendedSkill(
            id="baseline",
            name=meta["name"],
            author=meta["author"],
            kind=meta["kind"],
            command_arg="baseline",
            reason_zh=meta["reason_zh"],
            reason_en=meta["reason_en"],
            source="local",
            source_kind="local_baseline",
            score=10_000,
        )

    skill_meta = next((s for s in catalog.get("skills", []) if s["id"] == skill_id), None)
    if not skill_meta:
        raise KeyError(f"Skill {skill_id!r} not found in catalog")
    copy = CATALOG_COPY.get(skill_id, {})
    reason_zh = reason_override or copy.get("reason_zh") or skill_meta.get("notes", "")
    source = skill_meta.get("source", "")
    if source and skill_meta.get("subdir"):
        source = f"{source}#{skill_meta['subdir']}"
    return RecommendedSkill(
        id=skill_id,
        name=skill_meta["name"],
        author=skill_meta.get("author", ""),
        kind="catalog",
        command_arg=skill_id,
        reason_zh=reason_zh,
        reason_en=copy.get("reason_en") or skill_meta.get("approach", ""),
        source=source,
        source_kind="local_curated",
        score=85,
        stars=int(skill_meta.get("approx_stars") or 0) if str(skill_meta.get("approx_stars") or "").isdigit() else 0,
    )


def _append_unique(candidates: list[RecommendedSkill], candidate: RecommendedSkill, max_candidates: int) -> None:
    if len(candidates) >= max_candidates:
        return
    if any(existing.command_arg == candidate.command_arg or existing.id == candidate.id for existing in candidates):
        return
    candidates.append(candidate)


def _artifact_pipeline(pipeline_id: str) -> RecommendedSkill:
    return ARTIFACT_PIPELINES[pipeline_id]


def _figure_artifact_pipeline(pipeline_id: str) -> RecommendedSkill:
    return FIGURE_ARTIFACT_PIPELINES[pipeline_id]


def _candidate_key(candidate: RecommendedSkill) -> str:
    raw = (candidate.command_arg if "#" in (candidate.command_arg or "") else candidate.source) or candidate.command_arg or candidate.id
    source = raw.lower().strip()
    if source.startswith("http") and "#" not in source:
        source = source.rstrip("/")
    return source


def _is_external_candidate(candidate: RecommendedSkill) -> bool:
    return candidate.source_kind.startswith("github") or candidate.source_kind == "known_github"


def _rank_and_limit(candidates: list[RecommendedSkill], max_candidates: int) -> list[RecommendedSkill]:
    deduped: list[RecommendedSkill] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = _candidate_key(candidate)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)

    baseline = [
        candidate for candidate in deduped
        if candidate.id == "baseline" or candidate.id.startswith("baseline") or candidate.kind == "baseline"
    ]
    others = [candidate for candidate in deduped if candidate not in baseline]
    others.sort(key=lambda candidate: (candidate.score, candidate.stars), reverse=True)

    selected = (baseline[:1] + others)[:max_candidates]
    online = [candidate for candidate in others if _is_external_candidate(candidate)]
    has_online = any(_is_external_candidate(candidate) for candidate in selected)
    if online and not has_online and len(selected) >= max_candidates and max_candidates > 1:
        selected[-1] = online[0]
    elif online and not has_online:
        selected.append(online[0])
    return selected[:max_candidates]


def _detect_figure_family(task_text: str) -> str:
    compact = _compact(task_text)
    if any(word in compact for word in ["graphicalabstract", "图文摘要", "视觉摘要"]):
        return "graphical_abstract"
    if any(word in compact for word in ["csv", "excel", "数据", "data", "plot", "曲线", "柱状图", "散点", "箱线", "热图"]):
        return "plot"
    if any(word in compact for word in ["机制图", "架构图", "示意图", "流程图", "schematic", "diagram", "architecture", "workflow"]):
        return "schematic"
    return "mixed"


def _figure_artifact_command(candidates: list[RecommendedSkill]) -> list[str]:
    command = ["python3", "scripts/figure_artifact.py", "--input", "<input.txt>"]
    for candidate in candidates:
        if candidate.id in FIGURE_ARTIFACT_PIPELINES:
            command.extend(["--pipeline", candidate.id])
        elif candidate.command_arg.startswith(("http://", "https://", "/", "./", "~/")):
            command.extend(["--skill-source", candidate.command_arg])
    command.extend(["--run", "--judge", "--render-report", "--report-output", "./figure-artifact-report.html"])
    return command


def _note_if_no_new_external(candidates: list[RecommendedSkill], notes_zh: list[str], notes_en: list[str]) -> None:
    if not any(_is_external_candidate(candidate) for candidate in candidates):
        notes_zh.append("外部发现候选与本地 curated 候选去重后没有新增项，最终 shortlist 暂时只包含本地候选。")
        notes_en.append("After deduping external discovery against local curated candidates, no new external candidate remained in the shortlist.")


def recommend_candidates(
    task_text: str,
    domain: str = "academic-writing",
    max_candidates: int = 5,
    online_discovery: bool = True,
    local_only: Optional[bool] = None,
) -> Recommendation:
    """Return a small candidate set for the user's task description."""
    catalog = load_catalog(domain)
    signals = detect_task_signals(task_text)
    deliverable_type = detect_deliverable_type(task_text, signals)
    compare_mode = "artifact" if deliverable_type in {"pptx", "visual_artifact"} else "text"
    signal_set = set(signals)
    candidates: list[RecommendedSkill] = []
    notes_zh: list[str] = []
    notes_en: list[str] = []
    discovery_queries: list[str] = []
    local_only = wants_local_only(task_text) if local_only is None else local_only
    online_enabled = (
        online_discovery
        and not local_only
        and os.environ.get("FORKPROBE_DISCOVERY_OFFLINE") != "1"
        and discover_online_skills is not None
    )
    pool_limit = max(max_candidates * 3, 12)

    def add_catalog(skill_id: str, reason_override: Optional[str] = None) -> None:
        _append_unique(candidates, _catalog_skill(skill_id, catalog, reason_override), pool_limit)

    def add_byo(skill_id: str) -> None:
        candidate = BYO_COPY[skill_id]
        candidate.source = candidate.command_arg
        candidate.source_kind = "known_github"
        candidate.score = candidate.score or 78
        _append_unique(candidates, candidate, pool_limit)

    def add_pipeline(pipeline_id: str) -> None:
        candidate = _artifact_pipeline(pipeline_id)
        candidate.score = candidate.score or 76
        _append_unique(candidates, candidate, pool_limit)

    def add_figure_pipeline(pipeline_id: str) -> None:
        candidate = _figure_artifact_pipeline(pipeline_id)
        candidate.score = candidate.score or 76
        _append_unique(candidates, candidate, pool_limit)

    def add_online_candidates() -> None:
        nonlocal discovery_queries
        if local_only:
            notes_zh.append("用户要求只用本地候选，已跳过 GitHub/网络 discovery。")
            notes_en.append("User requested local-only candidates, so GitHub/network discovery was skipped.")
            return
        if not online_enabled:
            notes_zh.append("当前环境未启用 GitHub/网络 discovery，已使用本地 curated 候选。")
            notes_en.append("GitHub/network discovery is not enabled in this environment; using local curated candidates.")
            return
        discovery = discover_online_skills(
            deliverable=deliverable_type,
            signals=signals,
            limit=max(1, min(3, max_candidates - 1)),
        )
        discovery_queries = list(getattr(discovery, "queries", []))
        for candidate in getattr(discovery, "candidates", []):
            _append_unique(candidates, _skill_from_online_discovery(candidate, deliverable_type), pool_limit)
        notes_zh.extend(getattr(discovery, "notes_zh", []))
        notes_en.extend(getattr(discovery, "notes_en", []))

    if deliverable_type == "pptx":
        if discover_skill_pipelines:
            discovery = discover_skill_pipelines(
                deliverable="pptx",
                query=task_text,
                limit=max_candidates,
                local_only=False,
            )
            for pipeline in discovery.shortlist:
                _append_unique(candidates, _pipeline_from_discovery(pipeline), pool_limit)
            notes_zh.extend(discovery.notes_zh)
            notes_en.extend(discovery.notes_en)
        else:
            add_pipeline("baseline-presentations")
            add_pipeline("nature-paper2ppt-presentations")
            add_pipeline("pptx-direct")
            add_pipeline("storyboard-presentations")
        add_online_candidates()
        candidates = _rank_and_limit(candidates, max_candidates)
        _note_if_no_new_external(candidates, notes_zh, notes_en)
        notes_zh.append("这是 PPTX 成品对比模式：确认后应让每条 pipeline 各生成一个 .pptx，再用文件链接/缩略图/AI 评审并排比较。")
        notes_zh.append("不要把任务改写成“不要生成 PPTX”的大纲任务，除非用户明确只想先看方案。")
        notes_en.append("This is PPTX artifact comparison mode: each pipeline should generate its own .pptx, then compare files/previews/judge notes side by side.")
        notes_en.append("Do not rewrite this as an outline-only task unless the user explicitly asks for a plan only.")
        return Recommendation(
            deliverable_type=deliverable_type,
            compare_mode=compare_mode,
            task_signals=signals,
            candidates=candidates,
            notes_zh=notes_zh,
            notes_en=notes_en,
            suggested_command=[],
            mode_explanation_zh="识别到最终交付物是 PPTX 文件，应比较 PPT 生成 pipeline，而不是只比较 PPT 方案文字。",
            mode_explanation_en="Detected a PPTX deliverable. Compare PPT generation pipelines, not just outline text.",
            discovery_queries=discovery_queries,
        )

    if deliverable_type == "visual_artifact":
        figure_family = _detect_figure_family(task_text)
        if figure_family == "plot":
            for pipeline_id in ["baseline-python-figure", "plot-code-python", "nature-figure-python", "schematic-svg"]:
                add_figure_pipeline(pipeline_id)
        elif figure_family == "schematic":
            for pipeline_id in ["baseline-python-figure", "schematic-svg", "nature-figure-python", "graphical-abstract-svg"]:
                add_figure_pipeline(pipeline_id)
        elif figure_family == "graphical_abstract":
            for pipeline_id in ["baseline-python-figure", "graphical-abstract-svg", "nature-figure-python", "schematic-svg"]:
                add_figure_pipeline(pipeline_id)
        else:
            for pipeline_id in ["baseline-python-figure", "nature-figure-python", "plot-code-python", "schematic-svg"]:
                add_figure_pipeline(pipeline_id)
        add_online_candidates()
        candidates = _rank_and_limit(candidates, max_candidates)
        _note_if_no_new_external(candidates, notes_zh, notes_en)
        notes_zh.append("这是论文作图/科研绘图成品对比模式：确认后应让每条 pipeline 各生成一个 figure package，再用 artifact report 展示 PNG 预览、SVG/PDF/TIFF、代码、caption 和 QA。")
        notes_zh.append("如果用户明确只想比较图注、storyline 或说明文字，应切回 text 模式。")
        notes_en.append("This is scientific figure artifact comparison mode: each pipeline should generate its own figure package, then compare PNG previews, SVG/PDF/TIFF, code, caption, and QA notes in the artifact report.")
        notes_en.append("If the user explicitly wants only captions, storyline, or explanatory text, switch back to text mode.")
        return Recommendation(
            deliverable_type=deliverable_type,
            compare_mode=compare_mode,
            task_signals=signals,
            candidates=candidates,
            notes_zh=notes_zh,
            notes_en=notes_en,
            suggested_command=_figure_artifact_command(candidates),
            mode_explanation_zh="识别到最终交付物是科研图/论文 figure 成品，应比较 figure 生成 pipeline，而不是只比较图注或说明文字。",
            mode_explanation_en="Detected a scientific figure deliverable. Compare figure-generation pipelines, not just captions or explanatory text.",
            discovery_queries=discovery_queries,
        )

    add_catalog("baseline")

    if "figure" in signal_set:
        add_byo("nature-figure")
        add_catalog("paper-writer-skill", "适合先梳理 figure narrative、结果逻辑和图注表达。")
        add_catalog("research-paper-writing-skills", "适合中文科研图注、结果描述和论文语境表达。")
        notes_zh.append("当前识别为图注/storyline/说明文字对比；如果最终要科研图成品，请切换到 figure artifact 模式。")
        notes_en.append("This is recognized as caption/storyline/explanatory text comparison; switch to figure artifact mode for finished scientific figures.")
    elif "slides" in signal_set:
        add_byo("nature-paper2ppt")
        add_catalog("paper-writer-skill", "适合把论文结构转成正式汇报逻辑。")
        add_catalog("research-paper-writing-skills", "适合中文科研汇报中的论文表达和结构。")
        notes_zh.append("当前识别为 PPT 方案/大纲对比；如果用户要 PPTX 成品，请切换到 artifact 模式比较生成 pipeline。")
        notes_en.append("This is recognized as PPT plan/outline comparison; switch to artifact mode for finished PPTX pipeline comparison.")
    elif "rebuttal" in signal_set:
        add_catalog("paper-writer-skill")
        add_byo("nature-response")
        add_catalog("writing-anti-ai", "适合让回复语气更自然、克制，减少模板感。")
        add_catalog("research-paper-writing-skills", "适合中文起草后再转成正式科研回复。")
    elif "english" in signal_set or "nature" in signal_set:
        add_catalog("paper-writer-skill")
        add_byo("nature-polishing")
        if "anti_ai" in signal_set:
            add_catalog("writing-anti-ai")
        else:
            add_catalog("humanizer")
        add_catalog("research-paper-writing-skills")
    elif "anti_ai" in signal_set:
        add_catalog("writing-anti-ai")
        if "english" in signal_set:
            add_catalog("humanizer")
        add_catalog("research-paper-writing-skills")
        add_catalog("paper-writer-skill")
    else:
        add_catalog("writing-anti-ai")
        add_catalog("research-paper-writing-skills")
        add_catalog("paper-writer-skill")
        if "zh" not in signal_set:
            add_catalog("humanizer")

    add_online_candidates()
    candidates = _rank_and_limit(candidates, max_candidates)
    _note_if_no_new_external(candidates, notes_zh, notes_en)

    command = ["python3", "scripts/compare.py", "--input", "<input.txt>"]
    for candidate in candidates:
        if candidate.runnable:
            command.extend(["--skill", candidate.command_arg])
    command.extend(["--judge", "--output", "./report.html"])

    return Recommendation(
        deliverable_type=deliverable_type,
        compare_mode=compare_mode,
        task_signals=signals,
        candidates=candidates,
        notes_zh=notes_zh,
        notes_en=notes_en,
        suggested_command=command,
        mode_explanation_zh="识别到文本产物或方案产物，可用 compare.py 做并排文本对比。",
        mode_explanation_en="Detected a text or planning deliverable; compare.py can run a side-by-side text comparison.",
        discovery_queries=discovery_queries,
    )


def format_text(recommendation: Recommendation, input_path: str = "<input.txt>", lang: str = "zh") -> str:
    command = list(recommendation.suggested_command)
    if "--input" in command:
        command[command.index("--input") + 1] = input_path
    command_text = " ".join(shlex.quote(part) for part in command)

    if lang == "en":
        if recommendation.compare_mode == "artifact":
            lines = ["forkprobe should compare artifact-generation pipelines. Please confirm or edit before running.", ""]
        else:
            lines = ["forkprobe can compare these skills. Please confirm or edit before running.", ""]
        lines.append(f"Deliverable: {recommendation.deliverable_type} · Mode: {recommendation.compare_mode}")
        if recommendation.mode_explanation_en:
            lines.append(recommendation.mode_explanation_en)
        lines.append("Signals: " + ", ".join(recommendation.task_signals))
        if recommendation.discovery_queries:
            lines.append("Discovery queries: " + " | ".join(recommendation.discovery_queries))
        lines.append("")
        for idx, candidate in enumerate(recommendation.candidates, start=1):
            author = f" · {candidate.author}" if candidate.author else ""
            lines.append(f"{idx}. {candidate.name}{author}")
            lines.append(f"   {candidate.reason_en}")
            if _is_external_candidate(candidate):
                source_label = "known GitHub" if candidate.source_kind == "known_github" else ("GitHub seed" if candidate.source_kind == "github_seed" else "GitHub discovery")
                stars = f" · {candidate.stars} stars" if candidate.stars else ""
                lines.append(f"   Source: {source_label} · score {candidate.score}/100{stars}")
            if candidate.pipeline_steps:
                lines.append(f"   Pipeline: {' → '.join(candidate.pipeline_steps)}")
            if candidate.caution_en:
                lines.append(f"   Note: {candidate.caution_en}")
        if recommendation.notes_en:
            lines.append("")
            lines.extend(f"Note: {note}" for note in recommendation.notes_en)
        lines.append("")
        if recommendation.compare_mode == "artifact":
            lines.append("After confirmation:")
            if recommendation.suggested_command:
                lines.append(command_text)
                lines.append("This creates one workspace per figure pipeline, runs candidates, judges the artifact summaries, and renders the report. You can also add files to a candidate's artifacts folder and re-render.")
            elif recommendation.deliverable_type == "pptx":
                lines.append("Generate one PPTX per pipeline, then render an artifact comparison report with file links/previews.")
            else:
                lines.append("Generate one artifact package per pipeline, then render an artifact comparison report with file links/previews.")
        else:
            lines.append("Suggested command after confirmation:")
            lines.append(command_text)
        return "\n".join(lines)

    if recommendation.compare_mode == "artifact":
        lines = ["forkprobe 应该并排比较这些文件生成 pipeline。请确认或增删后再运行。", ""]
    else:
        lines = ["forkprobe 可以先并排比较这组 skill。请确认或增删后再运行。", ""]
    lines.append(f"交付物: {recommendation.deliverable_type} · 模式: {recommendation.compare_mode}")
    if recommendation.mode_explanation_zh:
        lines.append(recommendation.mode_explanation_zh)
    lines.append("识别到的任务信号: " + ", ".join(recommendation.task_signals))
    if recommendation.discovery_queries:
        lines.append("外部发现 query: " + " | ".join(recommendation.discovery_queries))
    lines.append("")
    for idx, candidate in enumerate(recommendation.candidates, start=1):
        author = f" · {candidate.author}" if candidate.author else ""
        lines.append(f"{idx}. {candidate.name}{author}")
        lines.append(f"   {candidate.reason_zh}")
        if _is_external_candidate(candidate):
            source_label = "已知 GitHub" if candidate.source_kind == "known_github" else ("GitHub seed" if candidate.source_kind == "github_seed" else "GitHub discovery")
            stars = f" · {candidate.stars} stars" if candidate.stars else ""
            lines.append(f"   来源: {source_label} · score {candidate.score}/100{stars}")
        if candidate.pipeline_steps:
            lines.append(f"   Pipeline: {' → '.join(candidate.pipeline_steps)}")
        if candidate.caution_zh:
            lines.append(f"   注意: {candidate.caution_zh}")
    if recommendation.notes_zh:
        lines.append("")
        lines.extend(f"注意: {note}" for note in recommendation.notes_zh)
    lines.append("")
    if recommendation.compare_mode == "artifact":
        lines.append("确认后执行方式:")
        if recommendation.suggested_command:
            lines.append(command_text)
            lines.append("这会为每条科研图 pipeline 创建独立 workspace、试跑候选、评审 artifact 摘要并渲染 report；也可以手动补充某个候选的 artifacts 后重新渲染。")
        elif recommendation.deliverable_type == "pptx":
            lines.append("让每条 pipeline 各生成一个 PPTX，再用 artifact report 展示文件链接、关键页预览和 AI 评审。")
        else:
            lines.append("让每条 pipeline 各生成一个 artifact package，再用 artifact report 展示文件链接、预览和 AI 评审。")
    else:
        lines.append("确认后可运行:")
        lines.append(command_text)
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Recommend forkprobe candidate skills for a task")
    parser.add_argument("--input", help="Path to task input or task description")
    parser.add_argument("--text", help="Task description text. Used when --input is omitted")
    parser.add_argument("--domain", default="academic-writing", help="Catalog domain")
    parser.add_argument("--max-candidates", type=int, default=5, help="Maximum candidates including baseline")
    parser.add_argument("--lang", choices=["zh", "en"], default="zh", help="Output language")
    parser.add_argument("--local-only", action="store_true", help="Skip GitHub/network discovery and use local candidates only")
    parser.add_argument("--json", action="store_true", help="Print JSON instead of human-readable text")
    args = parser.parse_args()

    input_label = "<input.txt>"
    if args.input:
        input_path = Path(args.input)
        task_text = input_path.read_text(encoding="utf-8")
        input_label = str(input_path)
    elif args.text:
        task_text = args.text
    else:
        task_text = sys.stdin.read()

    recommendation = recommend_candidates(
        task_text=task_text,
        domain=args.domain,
        max_candidates=args.max_candidates,
        local_only=args.local_only,
    )
    if args.json:
        print(json.dumps(asdict(recommendation), ensure_ascii=False, indent=2))
    else:
        print(format_text(recommendation, input_path=input_label, lang=args.lang))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
