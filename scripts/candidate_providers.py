"""Candidate-source providers for ForkProbe skill discovery.

Providers return metadata only. They never install or execute a skill; the
existing confirmation gate and artifact/text runners remain responsible for
that. Remote providers receive a sanitized task profile rather than raw user
content.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

from skill_loader import _parse_yaml_frontmatter


PROJECT_DIR = Path(__file__).resolve().parent.parent
FORKPROBE_HOME = Path(os.environ.get("FORKPROBE_HOME", Path.home() / ".forkprobe")).expanduser()
DEFAULT_EVERMIND_API_BASE = "https://skillhub.evermind.ai"
_FALSE_VALUES = {"0", "false", "no", "off"}
_EXCLUDED_DIRS = {
    ".git",
    ".system",
    "__pycache__",
    "node_modules",
    "outputs",
    "tmp",
    "cache",
}


@dataclass(frozen=True)
class ProviderQuery:
    """Sanitized task profile safe to send to external discovery services."""

    deliverable: str
    signals: tuple[str, ...]
    terms: tuple[str, ...]

    @property
    def search_text(self) -> str:
        return " ".join(self.terms[:8]).strip()


@dataclass
class ProviderCandidate:
    id: str
    name: str
    description: str
    provider: str
    command_arg: str
    source: str
    score: int
    category: str = ""
    version: str = ""
    license: str = ""
    source_quality_score: float | None = None
    safety_status: str = "needs_verification"
    installed: bool = False
    local_path: str = ""
    fingerprint: str = ""
    tags: list[str] = field(default_factory=list)
    stars: int = 0
    runnable: bool = True


@dataclass
class ProviderResult:
    provider: str
    candidates: list[ProviderCandidate]
    notes_zh: list[str] = field(default_factory=list)
    notes_en: list[str] = field(default_factory=list)
    query: str = ""
    used_cache: bool = False


class CandidateProvider(Protocol):
    name: str

    def discover(self, query: ProviderQuery, limit: int = 3, refresh: bool = False) -> ProviderResult:
        ...


_DELIVERABLE_TERMS = {
    "text": ("writing", "editing"),
    "ppt_outline": ("presentation", "slides", "outline"),
    "pptx": ("presentation", "pptx", "slides"),
    "visual_artifact": ("scientific", "figure", "visualization"),
    "research_report": ("research", "report", "evidence"),
    "web_artifact": ("frontend", "website", "web", "design"),
    "video_artifact": ("video", "editing", "motion"),
    "image_prompt": ("image prompt", "visual style", "AI image", "prompt engineering"),
}

_SIGNAL_TERMS = {
    "anti_ai": ("humanize", "anti ai", "natural writing"),
    "chinese_academic": ("academic writing", "research paper"),
    "english": ("english writing",),
    "nature": ("nature journal", "scientific writing"),
    "rebuttal": ("reviewer response", "rebuttal"),
    "figure": ("figure", "caption"),
    "slides": ("presentation", "slides"),
    "research_report": ("research report", "evidence"),
    "web_artifact": ("frontend", "website"),
    "video_artifact": ("video", "editing"),
    "image_prompt": ("image prompt", "visual style", "prompt library"),
}

_DELIVERABLE_SIGNAL_ALLOWLIST = {
    "ppt_outline": {"slides", "nature", "english", "chinese_academic"},
    "pptx": {"slides", "nature", "english", "chinese_academic"},
    "visual_artifact": {"figure", "nature"},
    "research_report": {"research_report"},
    "web_artifact": {"web_artifact"},
    "video_artifact": {"video_artifact"},
    "image_prompt": {"image_prompt"},
}


def build_provider_query(deliverable: str, signals: list[str]) -> ProviderQuery:
    """Build a compact public query without copying raw task text."""
    terms: list[str] = list(_DELIVERABLE_TERMS.get(deliverable, ("agent", "skill")))
    allowed_signals = _DELIVERABLE_SIGNAL_ALLOWLIST.get(deliverable)
    for signal in signals:
        if allowed_signals is not None and signal not in allowed_signals:
            continue
        terms.extend(_SIGNAL_TERMS.get(signal, ()))
    deduped: list[str] = []
    for term in terms:
        normalized = " ".join(term.lower().split())
        if normalized and normalized not in deduped:
            deduped.append(normalized)
    return ProviderQuery(deliverable=deliverable, signals=tuple(signals), terms=tuple(deduped))


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip().lower()).strip("-")
    return cleaned or "skill"


def _frontmatter_value(frontmatter: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = frontmatter.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _description_from_body(body: str) -> str:
    paragraphs: list[str] = []
    for block in re.split(r"\n\s*\n", body):
        cleaned = " ".join(line.strip() for line in block.splitlines() if not line.lstrip().startswith("#"))
        if cleaned and not cleaned.startswith(("```", "---")):
            paragraphs.append(cleaned)
        if paragraphs:
            break
    return paragraphs[0][:600] if paragraphs else "Local SKILL.md package."


def _detect_license(skill_dir: Path, frontmatter: dict[str, Any]) -> str:
    declared = _frontmatter_value(frontmatter, "license", "licence")
    if declared:
        return declared
    for name in ("LICENSE", "LICENSE.md", "LICENSE.txt", "COPYING"):
        path = skill_dir / name
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")[:3000].lower()
        except OSError:
            continue
        if "apache license" in text:
            return "Apache-2.0"
        if "mit license" in text or "permission is hereby granted" in text:
            return "MIT"
        if "mozilla public license" in text:
            return "MPL"
        if "gnu general public license" in text:
            return "GPL"
        return "Present"
    return ""


def _tokenize(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9][a-z0-9._+-]{1,}|[\u4e00-\u9fff]{2,}", text.lower())
        if token not in {"skill", "agent", "use", "when", "with", "this", "that"}
    }


def _match_score(candidate_text: str, query: ProviderQuery) -> int:
    haystack = candidate_text.lower()
    query_tokens = _tokenize(" ".join(query.terms))
    candidate_tokens = _tokenize(haystack)
    overlap = len(query_tokens & candidate_tokens)
    phrase_hits = sum(1 for term in query.terms if term in haystack)
    if overlap == 0 and phrase_hits == 0:
        return 0
    return min(96, 58 + overlap * 7 + phrase_hits * 8)


def default_local_skill_roots(project_dir: Path | None = None) -> list[Path]:
    configured = os.environ.get("FORKPROBE_LOCAL_SKILL_ROOTS")
    if configured is not None:
        return [Path(value).expanduser() for value in configured.split(os.pathsep) if value.strip()]
    project = (project_dir or PROJECT_DIR).resolve()
    roots = [
        Path.home() / ".codex" / "skills",
        Path.home() / ".agents" / "skills",
        Path.home() / ".claude" / "skills",
        Path.home() / ".dsh" / "skills",
        project / ".codex" / "skills",
        project / ".agents" / "skills",
        project / ".claude" / "skills",
        project / ".dsh" / "skills",
        project / "skills",
    ]
    deduped: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = str(root.expanduser().absolute())
        if key not in seen:
            seen.add(key)
            deduped.append(root)
    return deduped


def _walk_skill_files(root: Path, max_depth: int = 6) -> list[Path]:
    if not root.is_dir():
        return []
    found: list[Path] = []
    for current, dirs, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        try:
            depth = len(current_path.relative_to(root).parts)
        except ValueError:
            continue
        dirs[:] = [
            directory for directory in dirs
            if directory not in _EXCLUDED_DIRS and not (current_path / directory).is_symlink()
        ]
        if depth >= max_depth:
            dirs[:] = []
        skill_name = next((name for name in ("SKILL.md", "skill.md", "Skill.md") if name in files), None)
        if skill_name:
            found.append(current_path / skill_name)
            dirs[:] = []
    return found


class LocalSkillProvider:
    name = "local_installed"

    def __init__(self, roots: list[Path] | None = None, index_path: Path | None = None):
        self.roots = roots if roots is not None else default_local_skill_roots()
        configured_index = os.environ.get("FORKPROBE_LOCAL_SKILL_INDEX")
        self.index_path = index_path or (
            Path(configured_index).expanduser() if configured_index else FORKPROBE_HOME / "index" / "local-skills.json"
        )

    def _scan(self) -> list[ProviderCandidate]:
        candidates: list[ProviderCandidate] = []
        seen_fingerprints: set[str] = set()
        for root in self.roots:
            expanded_root = root.expanduser()
            for skill_path in _walk_skill_files(expanded_root):
                try:
                    raw = skill_path.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                frontmatter, body = _parse_yaml_frontmatter(raw)
                fingerprint = hashlib.sha256(raw.encode("utf-8")).hexdigest()
                if fingerprint in seen_fingerprints:
                    continue
                seen_fingerprints.add(fingerprint)
                skill_dir = skill_path.parent.resolve()
                name = _frontmatter_value(frontmatter, "name", "title") or skill_dir.name
                description = _frontmatter_value(frontmatter, "description", "summary") or _description_from_body(body)
                declared_source = _frontmatter_value(frontmatter, "source", "repository", "homepage", "url")
                version = _frontmatter_value(frontmatter, "version")
                license_name = _detect_license(skill_dir, frontmatter)
                tags = [value for value in re.split(r"[,\s]+", _frontmatter_value(frontmatter, "tags", "keywords")) if value]
                candidates.append(ProviderCandidate(
                    id=f"local:{_slug(name)}-{fingerprint[:8]}",
                    name=name,
                    description=description,
                    provider=self.name,
                    command_arg=str(skill_dir),
                    source=declared_source or str(skill_dir),
                    score=0,
                    category="local_installed",
                    version=version,
                    license=license_name,
                    safety_status="locally_installed",
                    installed=True,
                    local_path=str(skill_dir),
                    fingerprint=fingerprint,
                    tags=tags,
                ))
        return candidates

    def _write_index(self, candidates: list[ProviderCandidate]) -> None:
        payload = {
            "schema_version": 1,
            "generated_at": int(time.time()),
            "roots": [str(path.expanduser()) for path in self.roots],
            "skills": [asdict(candidate) for candidate in candidates],
        }
        try:
            self.index_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.index_path.with_suffix(self.index_path.suffix + ".tmp")
            temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(self.index_path)
        except OSError:
            pass

    def discover(self, query: ProviderQuery, limit: int = 3, refresh: bool = False) -> ProviderResult:
        if os.environ.get("FORKPROBE_LOCAL_SKILLS", "1").lower() in _FALSE_VALUES:
            return ProviderResult(provider=self.name, candidates=[])
        candidates = self._scan()
        self._write_index(candidates)
        matched: list[ProviderCandidate] = []
        for candidate in candidates:
            candidate.score = _match_score(
                " ".join([candidate.name, candidate.description, candidate.category, " ".join(candidate.tags)]),
                query,
            )
            if candidate.score:
                matched.append(candidate)
        matched.sort(key=lambda candidate: (candidate.score, candidate.name.lower()), reverse=True)
        notes_zh = [f"已自动扫描本地 Skill 目录：发现 {len(candidates)} 个，匹配当前场景 {len(matched)} 个。"]
        notes_en = [f"Automatically scanned local Skill directories: found {len(candidates)}, matched {len(matched)} to this scene."]
        return ProviderResult(
            provider=self.name,
            candidates=matched[:limit],
            notes_zh=notes_zh,
            notes_en=notes_en,
            query=query.search_text,
        )


def _github_command_arg(source_url: str) -> str:
    """Convert a GitHub tree URL to ForkProbe's repo#subdir format."""
    parsed = urllib.parse.urlsplit(source_url)
    if parsed.hostname != "github.com":
        return source_url
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2:
        return source_url
    repo_url = f"https://github.com/{parts[0]}/{parts[1]}"
    if len(parts) >= 5 and parts[2] in {"tree", "blob"}:
        subdir_parts = parts[4:]
        if subdir_parts and subdir_parts[-1].lower() == "skill.md":
            subdir_parts = subdir_parts[:-1]
        if subdir_parts:
            return f"{repo_url}#{'/'.join(subdir_parts)}"
    return repo_url


class EverMindSkillHubProvider:
    name = "evermind"

    def __init__(self, api_base: str | None = None, cache_dir: Path | None = None, timeout: float | None = None):
        self.api_base = (api_base or os.environ.get("FORKPROBE_EVERMIND_API_BASE") or DEFAULT_EVERMIND_API_BASE).rstrip("/")
        self.cache_dir = cache_dir or FORKPROBE_HOME / "cache" / "evermind"
        self.timeout = timeout if timeout is not None else float(os.environ.get("FORKPROBE_EVERMIND_TIMEOUT", "5"))

    def _cache_path(self, query: ProviderQuery, limit: int) -> Path:
        key = hashlib.sha256(f"{query.search_text}|{limit}".encode("utf-8")).hexdigest()[:20]
        return self.cache_dir / f"{key}.json"

    @staticmethod
    def _read_cache(path: Path) -> dict[str, Any] | None:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    @staticmethod
    def _write_cache(path: Path, payload: dict[str, Any]) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            wrapped = {"cached_at": int(time.time()), "payload": payload}
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(wrapped, ensure_ascii=False), encoding="utf-8")
            temporary.replace(path)
        except OSError:
            pass

    def _fetch(self, query: ProviderQuery, limit: int) -> dict[str, Any]:
        parsed = urllib.parse.urlsplit(self.api_base)
        if parsed.scheme != "https" or parsed.username or parsed.password or not parsed.hostname:
            raise ValueError("EverMind API base must be a credential-free HTTPS URL")
        # Pull a slightly wider pool, then rerank locally by the sanitized task
        # profile. Hub quality alone can otherwise favor a high-quality but
        # adjacent Skill over the most relevant one.
        fetch_limit = max(10, min(limit * 5, 20))
        params = urllib.parse.urlencode({"q": query.search_text, "page": "1", "limit": str(fetch_limit)})
        request = urllib.request.Request(
            f"{self.api_base}/openapi/v1/skills/search?{params}",
            headers={"Accept": "application/json", "User-Agent": "forkprobe-skill-discovery"},
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    @staticmethod
    def _items(payload: dict[str, Any]) -> list[dict[str, Any]]:
        result = payload.get("result")
        if isinstance(result, dict) and isinstance(result.get("items"), list):
            return [item for item in result["items"] if isinstance(item, dict)]
        for key in ("items", "skills", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        return []

    def _candidate(self, item: dict[str, Any], query: ProviderQuery) -> ProviderCandidate | None:
        source_url = str(item.get("source_url") or item.get("repository_url") or "").strip()
        parsed_source = urllib.parse.urlsplit(source_url)
        source_parts = [part for part in parsed_source.path.split("/") if part]
        skill_id_parts = [part for part in str(item.get("skill_id") or "").split("/") if part]
        has_exact_subdir = len(source_parts) >= 5 and source_parts[2] in {"tree", "blob"}
        if parsed_source.hostname == "github.com" and len(skill_id_parts) > 2 and not has_exact_subdir:
            # The Hub knows the exact package, but a repository-root source URL
            # would make ForkProbe clone the repo and pick an arbitrary SKILL.md.
            # Keep these out until an exact source path is available.
            return None
        command_arg = _github_command_arg(source_url)
        runnable = command_arg.startswith(("https://github.com/", "https://gitlab.com/"))
        if not runnable:
            return None
        quality = item.get("quality_score")
        try:
            quality_number = max(0.0, min(1.0, float(quality))) if quality is not None else None
        except (TypeError, ValueError):
            quality_number = None
        name = str(item.get("name") or item.get("skill_id") or "EverMind skill").strip()
        hub_id = str(item.get("id") or item.get("skill_id") or hashlib.sha256(command_arg.encode()).hexdigest()[:12])
        tags = [str(tag) for tag in item.get("tags", []) if str(tag).strip()]
        relevance = _match_score(
            " ".join([
                name,
                str(item.get("description") or ""),
                str(item.get("category") or ""),
                " ".join(tags),
            ]),
            query,
        )
        if relevance == 0:
            return None
        combined_score = round(relevance * 0.65 + (quality_number or 0.0) * 100 * 0.35)
        return ProviderCandidate(
            id=f"evermind:{hub_id}",
            name=name,
            description=str(item.get("description") or "EverMind Skill Hub candidate."),
            provider=self.name,
            command_arg=command_arg,
            source=source_url,
            score=min(98, max(62, combined_score)),
            category=str(item.get("category") or "").lower(),
            version=str(item.get("version") or ""),
            license=str(item.get("license") or ""),
            source_quality_score=quality_number,
            safety_status="skillhub_curated",
            installed=False,
            tags=tags,
            stars=int(item.get("github_star") or item.get("stars") or 0),
            runnable=True,
        )

    def discover(self, query: ProviderQuery, limit: int = 3, refresh: bool = False) -> ProviderResult:
        if not query.search_text:
            return ProviderResult(provider=self.name, candidates=[])
        cache_path = self._cache_path(query, limit)
        cached = self._read_cache(cache_path)
        ttl = int(os.environ.get("FORKPROBE_EVERMIND_CACHE_TTL", "86400"))
        cache_fresh = bool(cached and time.time() - int(cached.get("cached_at", 0)) <= ttl)
        offline = (
            os.environ.get("FORKPROBE_DISCOVERY_OFFLINE") == "1"
            or os.environ.get("FORKPROBE_EVERMIND_OFFLINE") == "1"
        )
        payload: dict[str, Any] | None = None
        used_cache = False
        error: Exception | None = None
        if cached and cache_fresh and not refresh:
            payload = cached.get("payload")
            used_cache = True
        elif not offline:
            try:
                payload = self._fetch(query, limit)
                self._write_cache(cache_path, payload)
            except (OSError, ValueError, urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as exc:
                error = exc
        if payload is None and cached:
            payload = cached.get("payload")
            used_cache = True
        candidates = [candidate for item in self._items(payload or {}) if (candidate := self._candidate(item, query))]
        candidates.sort(key=lambda candidate: (candidate.score, candidate.stars), reverse=True)
        notes_zh: list[str] = []
        notes_en: list[str] = []
        if candidates:
            source = "缓存" if used_cache else "官方开放 API"
            source_en = "cache" if used_cache else "the official open API"
            notes_zh.append(f"EverMind Skill Hub 通过{source}返回 {len(candidates)} 个候选；查询只包含脱敏场景词。")
            notes_en.append(f"EverMind Skill Hub returned {len(candidates)} candidates via {source_en}; the query contained sanitized scene terms only.")
        elif offline:
            notes_zh.append("当前为离线模式，且没有可用的 EverMind 缓存。")
            notes_en.append("Offline mode is active and no EverMind cache is available.")
        elif error:
            notes_zh.append("EverMind Skill Hub 暂时不可用，已继续使用其他候选来源。")
            notes_en.append("EverMind Skill Hub was unavailable; recommendation continued with other providers.")
        return ProviderResult(
            provider=self.name,
            candidates=candidates[:limit],
            notes_zh=notes_zh,
            notes_en=notes_en,
            query=query.search_text,
            used_cache=used_cache,
        )
