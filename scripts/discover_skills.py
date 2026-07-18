"""
forkprobe skill discovery helper.

This script builds a shortlist of candidate skills/pipelines before forkprobe
runs comparisons. PPTX still has a static curated registry because artifact
pipelines need explicit roles. Text and open-ended tasks can also do a live
GitHub discovery pass using sanitized task signals, never the user's raw input.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).parent
PROJECT_DIR = SCRIPT_DIR.parent
CATALOG_DIR = PROJECT_DIR / "catalog"


@dataclass
class DiscoveredCandidate:
    id: str
    name: str
    role: str
    source: str
    summary_zh: str
    summary_en: str
    can_produce_pptx: bool
    needs_generator: bool
    pairs_with: list[str]
    recommended: bool
    risk_zh: str = ""
    risk_en: str = ""


@dataclass
class PipelineCandidate:
    id: str
    name: str
    role: str
    source: str
    components: list[str]
    summary_zh: str
    summary_en: str
    can_produce_pptx: bool
    executable_status: str
    risk_zh: str = ""
    risk_en: str = ""


@dataclass
class DiscoveryReport:
    deliverable: str
    query: str
    candidates: list[DiscoveredCandidate]
    shortlist: list[PipelineCandidate]
    rejected: list[DiscoveredCandidate] = field(default_factory=list)
    notes_zh: list[str] = field(default_factory=list)
    notes_en: list[str] = field(default_factory=list)


@dataclass
class OnlineSkillCandidate:
    id: str
    name: str
    source: str
    command_arg: str
    summary_zh: str
    summary_en: str
    score: int
    stars: int
    category: str
    skill_path: str = ""
    runnable: bool = True
    risk_zh: str = ""
    risk_en: str = ""


@dataclass
class OnlineDiscoveryReport:
    deliverable: str
    queries: list[str]
    candidates: list[OnlineSkillCandidate]
    notes_zh: list[str] = field(default_factory=list)
    notes_en: list[str] = field(default_factory=list)


def load_discovery_catalog(deliverable: str = "pptx") -> dict[str, Any]:
    if deliverable != "pptx":
        raise ValueError(f"Unsupported discovery deliverable: {deliverable}")
    path = CATALOG_DIR / "pptx-artifact-skills.json"
    if not path.exists():
        raise FileNotFoundError(f"Discovery catalog not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def sanitized_discovery_queries(deliverable: str, signals: list[str], max_queries: int = 3) -> list[str]:
    """Build public search queries from task type only, not user-provided content."""
    signal_set = set(signals)
    if deliverable == "pptx":
        queries = [
            "claude skill pptx presentation",
            "codex skill pptx presentation",
        ]
        if signal_set & {"chinese_academic", "nature", "english"}:
            queries.insert(0, "academic presentation pptx skill")
        return queries[:max_queries]
    if deliverable == "visual_artifact":
        queries = [
            "claude skill scientific figure",
            "codex skill visualization figure",
            "ai skill diagram generation",
        ]
        return queries[:max_queries]
    if deliverable == "web_artifact":
        queries = [
            "claude skill frontend design website",
            "codex skill web artifact html",
            "agent skill landing page dashboard",
        ]
        return queries[:max_queries]

    queries = []
    if "anti_ai" in signal_set:
        queries.append("claude skill anti ai writing humanize")
    if signal_set & {"chinese_academic", "rebuttal"}:
        queries.append("claude skill research paper writing academic")
    if signal_set & {"english", "nature"}:
        queries.append("claude skill nature paper polishing")
    if "slides" in signal_set:
        queries.append("claude skill presentation outline")
    queries.append("claude skill writing")
    deduped: list[str] = []
    for query in queries:
        if query not in deduped:
            deduped.append(query)
    return deduped[:max_queries]


def _github_headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "forkprobe-skill-discovery",
    }
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _read_json_url(url: str, timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(url, headers=_github_headers())
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _github_search_repositories(query: str, limit: int, timeout: float) -> list[dict[str, Any]]:
    search_query = f"{query} in:name,description,readme"
    params = urllib.parse.urlencode({
        "q": search_query,
        "sort": "stars",
        "order": "desc",
        "per_page": str(max(1, min(limit, 20))),
    })
    data = _read_json_url(f"https://api.github.com/search/repositories?{params}", timeout=timeout)
    return list(data.get("items", []))


def _github_skill_paths(repo: dict[str, Any], timeout: float) -> list[str]:
    full_name = repo.get("full_name") or ""
    default_branch = repo.get("default_branch") or "main"
    if not full_name:
        return []
    encoded_branch = urllib.parse.quote(default_branch, safe="")
    url = f"https://api.github.com/repos/{full_name}/git/trees/{encoded_branch}?recursive=1"
    try:
        data = _read_json_url(url, timeout=timeout)
    except (OSError, urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError):
        return []
    paths = []
    for item in data.get("tree", []):
        path = item.get("path", "")
        if item.get("type") == "blob" and path.endswith("SKILL.md"):
            paths.append(path)
    return sorted(paths, key=lambda path: (path.count("/"), path))


def _repo_slug(name: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", name.strip().lower()).strip("-")
    return cleaned or "github-skill"


def _score_repo(repo: dict[str, Any], deliverable: str, signals: list[str], skill_path: str) -> int:
    stars = int(repo.get("stargazers_count") or 0)
    text = " ".join(
        str(part or "").lower()
        for part in [
            repo.get("name"),
            repo.get("full_name"),
            repo.get("description"),
            " ".join(repo.get("topics") or []),
            skill_path,
        ]
    )
    score = 30 + min(35, int(math.log10(max(stars, 1)) * 12))
    if "skill" in text:
        score += 15
    if "claude" in text or "codex" in text or "agent" in text:
        score += 8
    if deliverable == "pptx" and any(word in text for word in ["ppt", "pptx", "presentation", "slide"]):
        score += 18
    if deliverable == "visual_artifact" and any(word in text for word in ["figure", "diagram", "visual", "chart"]):
        score += 18
    if deliverable == "web_artifact" and any(word in text for word in ["frontend", "website", "web", "html", "landing", "dashboard", "ui", "ux"]):
        score += 18
    if deliverable == "text" and any(word in text for word in ["writing", "paper", "polish", "academic", "humanize"]):
        score += 18
    if "anti_ai" in signals and any(word in text for word in ["anti", "humanize", "ai"]):
        score += 10
    if "chinese_academic" in signals and any(word in text for word in ["paper", "academic", "research", "sci"]):
        score += 10
    if "nature" in signals and "nature" in text:
        score += 10
    if repo.get("archived"):
        score -= 35
    return max(0, min(score, 100))


def _online_candidate_from_repo(
    repo: dict[str, Any],
    deliverable: str,
    signals: list[str],
    skill_path: str,
) -> OnlineSkillCandidate:
    html_url = repo.get("html_url") or ""
    subdir = str(Path(skill_path).parent)
    command_arg = html_url if subdir == "." else f"{html_url}#{subdir}"
    stars = int(repo.get("stargazers_count") or 0)
    score = _score_repo(repo, deliverable, signals, skill_path)
    name = repo.get("name") or repo.get("full_name") or "GitHub skill"
    description = repo.get("description") or "GitHub-discovered skill candidate with SKILL.md."
    return OnlineSkillCandidate(
        id=f"github:{_repo_slug(repo.get('full_name') or name)}",
        name=name,
        source=html_url,
        command_arg=command_arg,
        summary_zh=f"GitHub 发现候选，约 {stars} stars；{description}",
        summary_en=f"GitHub-discovered candidate, about {stars} stars; {description}",
        score=score,
        stars=stars,
        category="github_discovered",
        skill_path=skill_path,
        runnable=True,
        risk_zh="外部候选已发现 SKILL.md，但执行前仍建议检查 license、依赖和是否真的适配当前任务。",
        risk_en="External candidate has SKILL.md, but license, dependencies, and task fit should still be checked before execution.",
    )


def _seed_candidate(
    *,
    id: str,
    name: str,
    source: str,
    summary_zh: str,
    summary_en: str,
    score: int,
    stars: int = 0,
) -> OnlineSkillCandidate:
    return OnlineSkillCandidate(
        id=id,
        name=name,
        source=source.split("#", 1)[0],
        command_arg=source,
        summary_zh=summary_zh,
        summary_en=summary_en,
        score=score,
        stars=stars,
        category="github_seed",
        skill_path="SKILL.md",
        runnable=True,
        risk_zh="内置 GitHub seed 候选；执行前仍建议检查 license、依赖和任务适配度。",
        risk_en="Built-in GitHub seed candidate; license, dependencies, and task fit should still be checked before execution.",
    )


def _seed_candidates(deliverable: str, signals: list[str]) -> list[OnlineSkillCandidate]:
    signal_set = set(signals)
    seeds: list[OnlineSkillCandidate] = []

    if deliverable == "pptx" or "slides" in signal_set:
        seeds.extend([
            _seed_candidate(
                id="github_seed:academic-pptx-skill",
                name="academic-pptx-skill",
                source="https://github.com/Gabberflast/academic-pptx-skill",
                summary_zh="内置 GitHub seed：偏学术汇报结构、action title 和证据链。",
                summary_en="Built-in GitHub seed for academic deck structure, action titles, and evidence flow.",
                score=84,
            ),
            _seed_candidate(
                id="github_seed:nature-paper2ppt",
                name="nature-paper2ppt",
                source="https://github.com/Yuan1z0825/nature-skills#skills/nature-paper2ppt",
                summary_zh="内置 GitHub seed：论文转科研汇报结构，适合 Nature 风格或组会 PPT。",
                summary_en="Built-in GitHub seed for paper-to-deck planning and scientific presentations.",
                score=83,
            ),
        ])

    if deliverable == "visual_artifact" or "figure" in signal_set:
        seeds.append(_seed_candidate(
            id="github_seed:nature-figure",
            name="nature-figure",
            source="https://github.com/Yuan1z0825/nature-skills#skills/nature-figure",
            summary_zh="内置 GitHub seed：科研图、figure storyline 和图注构思。",
            summary_en="Built-in GitHub seed for scientific figures, figure storyline, and captions.",
            score=82,
        ))

    if deliverable == "web_artifact" or "web" in signal_set:
        seeds.extend([
            _seed_candidate(
                id="github_seed:anthropic-frontend-design",
                name="Anthropic frontend-design",
                source="https://github.com/anthropics/skills#skills/frontend-design",
                summary_zh="内置 GitHub seed：强调主题化视觉、排版、响应式和避免模板化 AI 页面。",
                summary_en="Built-in GitHub seed for subject-specific visuals, typography, responsiveness, and avoiding generic AI pages.",
                score=92,
                stars=162057,
            ),
            _seed_candidate(
                id="github_seed:garden-web-design-engineer",
                name="web-design-engineer",
                source="https://github.com/ConardLi/garden-skills#skills/web-design-engineer",
                summary_zh="内置 GitHub seed：完整 HTML/CSS/JavaScript/React 页面与浏览器验收流程。",
                summary_en="Built-in GitHub seed for complete HTML/CSS/JavaScript/React pages and browser acceptance.",
                score=89,
                stars=9595,
            ),
            _seed_candidate(
                id="github_seed:baoyu-design-web",
                name="baoyu-design",
                source="https://github.com/JimLiu/baoyu-design#skills/baoyu-design",
                summary_zh="内置 GitHub seed：自包含 HTML 高保真 UI、Landing Page 和 Dashboard。",
                summary_en="Built-in GitHub seed for self-contained high-fidelity HTML UI, landing pages, and dashboards.",
                score=87,
                stars=2649,
            ),
        ])

    if "rebuttal" in signal_set:
        seeds.append(_seed_candidate(
            id="github_seed:nature-response",
            name="nature-response",
            source="https://github.com/Yuan1z0825/nature-skills#skills/nature-response",
            summary_zh="内置 GitHub seed：返修、审稿人意见回复和 response letter。",
            summary_en="Built-in GitHub seed for revision responses and response letters.",
            score=82,
        ))

    if signal_set & {"english", "nature"}:
        seeds.append(_seed_candidate(
            id="github_seed:nature-polishing",
            name="nature-polishing",
            source="https://github.com/Yuan1z0825/nature-skills#skills/nature-polishing",
            summary_zh="内置 GitHub seed：Nature 风格英文润色、中译英和英文摘要优化。",
            summary_en="Built-in GitHub seed for Nature-style English polishing, translation, and abstracts.",
            score=84,
        ))

    if "anti_ai" in signal_set:
        seeds.append(_seed_candidate(
            id="github_seed:writing-anti-ai",
            name="writing-anti-ai",
            source="https://github.com/Galaxy-Dawn/claude-scholar#skills/writing-anti-ai",
            summary_zh="内置 GitHub seed：降低机器感、减少模板感的中英文写作 skill。",
            summary_en="Built-in GitHub seed for reducing AI-like phrasing in Chinese and English writing.",
            score=86,
            stars=400,
        ))

    if "chinese_academic" in signal_set:
        seeds.append(_seed_candidate(
            id="github_seed:research-paper-writing-skills",
            name="Research-Paper-Writing-Skills",
            source="https://github.com/Master-cai/Research-Paper-Writing-Skills#research-paper-writing",
            summary_zh="内置 GitHub seed：中文科研论文表达和 SCI 写作语气优化。",
            summary_en="Built-in GitHub seed for Chinese research-paper writing and SCI-style prose.",
            score=82,
        ))

    return seeds


def discover_online_skills(
    deliverable: str,
    signals: list[str],
    limit: int = 3,
    timeout: float | None = None,
) -> OnlineDiscoveryReport:
    """Discover runnable GitHub skill candidates with sanitized search queries."""
    if os.environ.get("FORKPROBE_DISCOVERY_OFFLINE") == "1":
        return OnlineDiscoveryReport(
            deliverable=deliverable,
            queries=[],
            candidates=[],
            notes_zh=["已按 FORKPROBE_DISCOVERY_OFFLINE=1 跳过 GitHub/网络 discovery。"],
            notes_en=["Skipped GitHub/network discovery because FORKPROBE_DISCOVERY_OFFLINE=1."],
        )

    timeout = timeout if timeout is not None else float(os.environ.get("FORKPROBE_DISCOVERY_TIMEOUT", "6"))
    queries = sanitized_discovery_queries(deliverable, signals)
    candidates: list[OnlineSkillCandidate] = []
    seen_sources: set[str] = set()
    errors: list[str] = []
    repo_fetch_limit = max(limit * 4, 8)

    for query in queries:
        try:
            repos = _github_search_repositories(query, limit=repo_fetch_limit, timeout=timeout)
        except (OSError, urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as exc:
            errors.append(f"{query}: {exc}")
            continue
        for repo in repos:
            html_url = repo.get("html_url") or ""
            if not html_url or html_url in seen_sources:
                continue
            skill_paths = _github_skill_paths(repo, timeout=timeout)
            if not skill_paths:
                continue
            candidate = _online_candidate_from_repo(repo, deliverable, signals, skill_paths[0])
            candidates.append(candidate)
            seen_sources.add(html_url)
            if len(candidates) >= limit:
                break
        if len(candidates) >= limit:
            break

    if len(candidates) < limit:
        for seed in _seed_candidates(deliverable, signals):
            if all(existing.command_arg != seed.command_arg for existing in candidates):
                candidates.append(seed)
            if len(candidates) >= limit:
                break

    candidates.sort(key=lambda candidate: (candidate.score, candidate.stars), reverse=True)
    notes_zh = [
        "已用脱敏任务信号做 GitHub/网络 discovery，没有把原文内容放进搜索 query。",
    ]
    notes_en = [
        "GitHub/network discovery used sanitized task signals only; raw user content was not searched.",
    ]
    has_realtime = any(candidate.category == "github_discovered" for candidate in candidates)
    has_seed = any(candidate.category == "github_seed" for candidate in candidates)
    if has_realtime:
        notes_zh.append("GitHub 候选已验证存在 SKILL.md，但仍需在执行前做 license/依赖/适配检查。")
        notes_en.append("GitHub candidates have SKILL.md, but license/dependency/task-fit checks are still recommended before execution.")
    else:
        notes_zh.append("GitHub discovery 未发现通过 SKILL.md 验证的新候选，已保留本地 curated 候选。")
        notes_en.append("GitHub discovery found no new candidates verified with SKILL.md; keeping local curated candidates.")
    if has_seed:
        notes_zh.append("已加入内置 GitHub seed 候选作为外部参考；seed 不是实时热门搜索结果。")
        notes_en.append("Added built-in GitHub seed candidates as external references; seeds are not live trending results.")
    if errors:
        notes_zh.append("部分 GitHub 查询失败或被限流；可设置 GITHUB_TOKEN 后重试。")
        notes_en.append("Some GitHub queries failed or were rate-limited; set GITHUB_TOKEN and retry.")
    return OnlineDiscoveryReport(
        deliverable=deliverable,
        queries=queries,
        candidates=candidates[:limit],
        notes_zh=notes_zh,
        notes_en=notes_en,
    )


def _candidate_from_meta(meta: dict[str, Any]) -> DiscoveredCandidate:
    return DiscoveredCandidate(
        id=meta["id"],
        name=meta["name"],
        role=meta["role"],
        source=meta.get("source", ""),
        summary_zh=meta.get("summary_zh", ""),
        summary_en=meta.get("summary_en", ""),
        can_produce_pptx=bool(meta.get("can_produce_pptx")),
        needs_generator=bool(meta.get("needs_generator")),
        pairs_with=list(meta.get("pairs_with", [])),
        recommended=bool(meta.get("recommended")),
        risk_zh=meta.get("risk_zh", ""),
        risk_en=meta.get("risk_en", ""),
    )


def _pipeline_id(parts: list[str]) -> str:
    return "+".join(parts)


def _pipeline_name(parts: list[str], by_id: dict[str, DiscoveredCandidate]) -> str:
    return " + ".join(by_id[p].name if p in by_id else p for p in parts)


def _compose_pipeline(parts: list[str], by_id: dict[str, DiscoveredCandidate]) -> PipelineCandidate:
    known = [by_id[p] for p in parts if p in by_id]
    missing = [p for p in parts if p not in by_id]
    role = "pipeline"
    sources = [c.source for c in known if c.source]
    summaries_zh = [c.summary_zh for c in known if c.summary_zh]
    summaries_en = [c.summary_en for c in known if c.summary_en]
    risks_zh = [c.risk_zh for c in known if c.risk_zh]
    risks_en = [c.risk_en for c in known if c.risk_en]
    can_produce = any(c.can_produce_pptx for c in known)
    needs_external_verification = any(
        c.source.startswith("http") and c.id not in {"nature-paper2ppt"}
        for c in known
    )
    if missing:
        executable_status = "missing_component"
        can_produce = False
    elif needs_external_verification:
        executable_status = "needs_verification"
    else:
        executable_status = "ready_or_local"

    return PipelineCandidate(
        id=_pipeline_id(parts),
        name=_pipeline_name(parts, by_id),
        role=role,
        source=" + ".join(sources) or "local",
        components=parts,
        summary_zh="；".join(summaries_zh),
        summary_en="; ".join(summaries_en),
        can_produce_pptx=can_produce,
        executable_status=executable_status,
        risk_zh="；".join(risks_zh),
        risk_en="; ".join(risks_en),
    )


def _default_shortlist_ids(catalog: dict[str, Any], query: str, local_only: bool) -> list[str]:
    shortlists = catalog.get("default_shortlists", {})
    if local_only:
        return list(shortlists.get("local_only", []))
    query_l = query.lower()
    if any(word in query_l for word in ["academic", "科研", "论文", "实验室", "lab", "science", "scientific"]):
        return list(shortlists.get("scientific_pptx", []))
    return list(shortlists.get("scientific_pptx", []))


def discover(
    deliverable: str = "pptx",
    query: str = "",
    limit: int = 5,
    local_only: bool = False,
) -> DiscoveryReport:
    catalog = load_discovery_catalog(deliverable)
    candidates = [_candidate_from_meta(meta) for meta in catalog.get("candidates", [])]
    by_id = {candidate.id: candidate for candidate in candidates}

    shortlist: list[PipelineCandidate] = []
    for spec in _default_shortlist_ids(catalog, query=query, local_only=local_only):
        parts = spec.split("+")
        shortlist.append(_compose_pipeline(parts, by_id))
        if len(shortlist) >= limit:
            break

    shortlisted_components = {component for pipeline in shortlist for component in pipeline.components}
    rejected = [
        candidate for candidate in candidates
        if candidate.id not in shortlisted_components and not candidate.recommended
    ]

    notes_zh = [
        "只把能形成 PPTX 成品的完整 pipeline 放进 shortlist；strategy-only skill 必须搭配 generator。",
        "外部 GitHub 候选进入执行前仍需做 clone/依赖/license/产物路径检查。",
    ]
    notes_en = [
        "Only complete PPTX-producing pipelines are shortlisted; strategy-only skills must be paired with a generator.",
        "External GitHub candidates still need clone/dependency/license/output-path checks before execution.",
    ]
    return DiscoveryReport(
        deliverable=deliverable,
        query=query,
        candidates=candidates,
        shortlist=shortlist,
        rejected=rejected,
        notes_zh=notes_zh,
        notes_en=notes_en,
    )


def format_report(report: DiscoveryReport, lang: str = "zh") -> str:
    if lang == "en":
        lines = [
            "forkprobe skill discovery report",
            f"Deliverable: {report.deliverable}",
            f"Query: {report.query or '(none)'}",
            "",
            "Recommended shortlist:",
        ]
        for idx, pipeline in enumerate(report.shortlist, start=1):
            lines.append(f"{idx}. {pipeline.name}")
            lines.append(f"   Components: {' → '.join(pipeline.components)}")
            lines.append(f"   Status: {pipeline.executable_status}")
            lines.append(f"   Why: {pipeline.summary_en}")
            if pipeline.risk_en:
                lines.append(f"   Risk: {pipeline.risk_en}")
        if report.rejected:
            lines.append("")
            lines.append("Not shortlisted / needs more verification:")
            for candidate in report.rejected:
                lines.append(f"- {candidate.name}: {candidate.risk_en or candidate.summary_en}")
        if report.notes_en:
            lines.append("")
            lines.extend(f"Note: {note}" for note in report.notes_en)
        return "\n".join(lines)

    lines = [
        "forkprobe skill discovery report",
        f"交付物: {report.deliverable}",
        f"查询: {report.query or '(无)'}",
        "",
        "推荐 shortlist:",
    ]
    for idx, pipeline in enumerate(report.shortlist, start=1):
        lines.append(f"{idx}. {pipeline.name}")
        lines.append(f"   组件: {' → '.join(pipeline.components)}")
        lines.append(f"   状态: {pipeline.executable_status}")
        lines.append(f"   理由: {pipeline.summary_zh}")
        if pipeline.risk_zh:
            lines.append(f"   风险: {pipeline.risk_zh}")
    if report.rejected:
        lines.append("")
        lines.append("未进入 shortlist / 需要更多验证:")
        for candidate in report.rejected:
            lines.append(f"- {candidate.name}: {candidate.risk_zh or candidate.summary_zh}")
    if report.notes_zh:
        lines.append("")
        lines.extend(f"注意: {note}" for note in report.notes_zh)
    return "\n".join(lines)


def format_online_report(report: OnlineDiscoveryReport, lang: str = "zh") -> str:
    if lang == "en":
        lines = [
            "forkprobe online skill discovery report",
            f"Deliverable: {report.deliverable}",
            f"Queries: {' | '.join(report.queries) if report.queries else '(none)'}",
            "",
            "Recommended GitHub candidates:",
        ]
        if not report.candidates:
            lines.append("(none)")
        for idx, candidate in enumerate(report.candidates, start=1):
            lines.append(f"{idx}. {candidate.name}")
            lines.append(f"   Source: {candidate.command_arg}")
            lines.append(f"   Score: {candidate.score}/100 · Stars: {candidate.stars}")
            lines.append(f"   Why: {candidate.summary_en}")
            if candidate.risk_en:
                lines.append(f"   Note: {candidate.risk_en}")
        if report.notes_en:
            lines.append("")
            lines.extend(f"Note: {note}" for note in report.notes_en)
        return "\n".join(lines)

    lines = [
        "forkprobe 在线 skill discovery report",
        f"交付物: {report.deliverable}",
        f"查询: {' | '.join(report.queries) if report.queries else '(无)'}",
        "",
        "推荐 GitHub 候选:",
    ]
    if not report.candidates:
        lines.append("(无)")
    for idx, candidate in enumerate(report.candidates, start=1):
        lines.append(f"{idx}. {candidate.name}")
        lines.append(f"   来源: {candidate.command_arg}")
        lines.append(f"   评分: {candidate.score}/100 · Stars: {candidate.stars}")
        lines.append(f"   理由: {candidate.summary_zh}")
        if candidate.risk_zh:
            lines.append(f"   注意: {candidate.risk_zh}")
    if report.notes_zh:
        lines.append("")
        lines.extend(f"注意: {note}" for note in report.notes_zh)
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Discover candidate skills/pipelines before forkprobe comparison")
    parser.add_argument("--deliverable", default="pptx", choices=["text", "pptx", "visual_artifact", "research_report", "web_artifact"], help="Deliverable type")
    parser.add_argument("--query", default="", help="Task/domain query, e.g. 'academic PPT from document'")
    parser.add_argument("--signal", action="append", default=[], help="Sanitized task signal, e.g. anti_ai or chinese_academic")
    parser.add_argument("--limit", type=int, default=5, help="Maximum pipelines in shortlist")
    parser.add_argument("--local-only", action="store_true", help="Only shortlist local/bundled candidates")
    parser.add_argument("--online", action="store_true", help="Run GitHub/network discovery using sanitized signals")
    parser.add_argument("--lang", choices=["zh", "en"], default="zh", help="Output language")
    parser.add_argument("--json", action="store_true", help="Print JSON")
    args = parser.parse_args()

    if args.online or args.deliverable != "pptx":
        report = discover_online_skills(
            deliverable=args.deliverable,
            signals=args.signal or ["general"],
            limit=args.limit,
        )
        if args.json:
            print(json.dumps(asdict(report), ensure_ascii=False, indent=2))
        else:
            print(format_online_report(report, lang=args.lang))
        return 0

    report = discover(
        deliverable=args.deliverable,
        query=args.query,
        limit=args.limit,
        local_only=args.local_only,
    )
    if args.json:
        print(json.dumps(asdict(report), ensure_ascii=False, indent=2))
    else:
        print(format_report(report, lang=args.lang))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
