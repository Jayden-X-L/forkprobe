"""
Skill loading: fetch a skill from GitHub or local path, parse its SKILL.md,
extract a usable system prompt.

Catalog skills are cached under ~/.forkprobe-cache/<slug>/ so we don't re-clone
on every run. BYO paths are read directly.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

CACHE_DIR = Path(os.path.expanduser("~/.forkprobe-cache"))


@dataclass
class LoadedSkill:
    """A skill that has been fetched and parsed."""
    id: str
    name: str
    description: str          # from SKILL.md frontmatter
    body: str                 # markdown body of SKILL.md (the instructions)
    source: str               # original URL/path
    local_path: Path          # where it lives on disk
    raw_frontmatter: dict     # parsed YAML

    def to_system_prompt(self) -> str:
        """
        Compose a system prompt that makes a model behave 'as if it had this skill'.

        Strategy: combine the description (which tells the model when/why) and the
        body (which tells it how). Wrapped in a clear instruction so the model
        treats this as procedural guidance, not as user content.
        """
        return (
            f"You are a helpful assistant. You have been given the following skill to apply "
            f"to the user's task. Follow its instructions carefully.\n\n"
            f"# Skill: {self.name}\n\n"
            f"Skill package path: {self.local_path.parent}\n"
            f"If the skill instructions refer to local files such as manifests, static layers, "
            f"references, scripts, or assets, resolve those paths relative to this package path. "
            f"You may read those files when needed, but do not modify files.\n\n"
            f"## Purpose\n{self.description}\n\n"
            f"## Instructions\n{self.body}\n\n"
            f"---\nNow apply this skill to the user's input."
        )


# --- Frontmatter parsing ---

_FRONTMATTER_RE = re.compile(
    r"^---\s*\n(.*?)\n---\s*\n(.*)$",
    re.DOTALL,
)


def _parse_yaml_frontmatter(text: str) -> tuple[dict, str]:
    """
    Minimal YAML-frontmatter parser. Avoids a hard dependency on pyyaml.

    Returns (frontmatter_dict, body). If no frontmatter present, returns ({}, original_text).
    """
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text

    raw_fm, body = m.group(1), m.group(2)
    fm: dict = {}
    current_key: Optional[str] = None
    current_lines: list[str] = []

    # Very simple parser: handles `key: value` and `key: |` block scalars.
    # If a skill uses fancier YAML, we'd swap in pyyaml.
    for line in raw_fm.splitlines():
        if not line.strip():
            continue
        if line.startswith((" ", "\t")) and current_key is not None:
            current_lines.append(line.strip())
            continue

        # Flush previous block scalar
        if current_key is not None and current_lines:
            fm[current_key] = " ".join(current_lines).strip()
            current_lines = []

        if ":" in line:
            key, _, val = line.partition(":")
            key = key.strip()
            val = val.strip()
            if val in ("|", ">"):
                current_key = key
                current_lines = []
            else:
                # Strip surrounding quotes if any
                if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                    val = val[1:-1]
                fm[key] = val
                current_key = None

    # Flush trailing block scalar
    if current_key is not None and current_lines:
        fm[current_key] = " ".join(current_lines).strip()

    return fm, body.strip()


# --- SKILL.md discovery ---

_SKILL_MD_CANDIDATES = ["SKILL.md", "skill.md", "Skill.md"]


def _find_skill_md(root: Path) -> Optional[Path]:
    """Find SKILL.md within a directory, preferring root level. Falls back to first subdir match."""
    # 1. Root level
    for candidate in _SKILL_MD_CANDIDATES:
        p = root / candidate
        if p.exists():
            return p

    # 2. Walk depth-first, return shallowest match
    matches: list[tuple[int, Path]] = []
    for path in root.rglob("*"):
        if path.is_file() and path.name in _SKILL_MD_CANDIDATES:
            depth = len(path.relative_to(root).parts)
            matches.append((depth, path))
    if matches:
        matches.sort(key=lambda x: x[0])
        return matches[0][1]

    return None


# --- Fetching ---

def _slug_from_url(url: str) -> str:
    """Stable per-URL cache key (no collisions, short)."""
    h = hashlib.sha256(url.encode("utf-8")).hexdigest()[:10]
    # Try to keep a human-readable suffix
    suffix = url.rstrip("/").split("/")[-1].replace(".git", "")
    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", suffix)[:40]
    return f"{safe}-{h}"


def _git_clone(url: str, dest: Path, depth: int = 1) -> None:
    """Shallow clone a public repo. Raises CalledProcessError on failure."""
    subprocess.run(
        ["git", "clone", "--depth", str(depth), "--quiet", url, str(dest)],
        check=True,
        timeout=60,
        capture_output=True,
    )


def fetch_skill(source: str, force_refresh: bool = False) -> Path:
    """
    Fetch a skill to a local directory and return the path.

    Source can be:
      - HTTPS URL to a GitHub/GitLab repo
      - Local directory path
      - Local SKILL.md file path

    Cached under ~/.forkprobe-cache/.
    """
    source = source.strip()

    # Local path
    if source.startswith("/") or source.startswith("./") or source.startswith("~/"):
        p = Path(os.path.expanduser(source))
        if not p.exists():
            raise FileNotFoundError(f"Skill path does not exist: {p}")
        return p.parent if p.is_file() else p

    # GitHub URL → clone
    if source.startswith(("http://", "https://", "git@")):
        # Normalize trailing /tree/main or /blob/main URLs to bare repo
        bare = re.sub(r"/(tree|blob)/[^/]+/?.*$", "", source)
        if not bare.endswith(".git") and "github.com" in bare:
            bare = bare + ".git"

        CACHE_DIR.mkdir(exist_ok=True)
        slug = _slug_from_url(bare)
        dest = CACHE_DIR / slug

        if dest.exists() and force_refresh:
            shutil.rmtree(dest)

        if not dest.exists():
            _git_clone(bare, dest)
        return dest

    raise ValueError(f"Unrecognized skill source: {source!r}")


# --- Top-level load function ---

def load_skill(
    skill_id: str,
    source: str,
    subdir: Optional[str] = None,
    force_refresh: bool = False,
) -> LoadedSkill:
    """
    Fetch + parse a skill. Used by compare.py's resolve_skill().

    Args:
        skill_id: catalog ID (also used as cache key fallback)
        source: GitHub URL, local dir, or local SKILL.md path
        subdir: optional path within source where the target SKILL.md lives
                (e.g. "skills/writing-anti-ai" for multi-skill repos)
        force_refresh: re-clone even if cached

    Raises:
        FileNotFoundError: SKILL.md not found in the source/subdir
        ValueError: malformed source
    """
    root = fetch_skill(source, force_refresh=force_refresh)
    search_root = root / subdir if subdir else root
    if not search_root.exists():
        raise FileNotFoundError(
            f"Subdir {subdir!r} not found in {root}. "
            f"Check the catalog's subdir field."
        )
    skill_md = _find_skill_md(search_root)
    if skill_md is None:
        raise FileNotFoundError(
            f"No SKILL.md found in {search_root} (searched root + subdirs). "
            f"This skill may not be Anthropic-format compatible."
        )

    text = skill_md.read_text(encoding="utf-8")
    frontmatter, body = _parse_yaml_frontmatter(text)

    name = frontmatter.get("name", skill_id)
    description = frontmatter.get("description", "")

    # If frontmatter is empty (no `---` block), treat the whole file as body
    if not description and not body.strip():
        body = text

    return LoadedSkill(
        id=skill_id,
        name=name,
        description=description,
        body=body,
        source=source,
        local_path=skill_md,
        raw_frontmatter=frontmatter,
    )


# --- CLI smoke test ---

if __name__ == "__main__":
    import sys
    import json

    if len(sys.argv) < 2:
        print("Usage: python skill_loader.py <github_url_or_path> [--refresh]")
        sys.exit(1)

    source = sys.argv[1]
    refresh = "--refresh" in sys.argv

    print(f"Loading skill from: {source}")
    try:
        skill = load_skill(skill_id="test", source=source, force_refresh=refresh)
        print(f"  ✓ Found SKILL.md at: {skill.local_path}")
        print(f"  ✓ Name: {skill.name}")
        print(f"  ✓ Description: {skill.description[:120]}{'...' if len(skill.description) > 120 else ''}")
        print(f"  ✓ Body length: {len(skill.body)} chars")
        print(f"  ✓ Frontmatter keys: {list(skill.raw_frontmatter.keys())}")
        print(f"\nFirst 500 chars of body:")
        print("  " + skill.body[:500].replace("\n", "\n  "))
        print(f"\nFirst 500 chars of generated system prompt:")
        print("  " + skill.to_system_prompt()[:500].replace("\n", "\n  "))
    except Exception as e:
        print(f"  ✗ Error: {type(e).__name__}: {e}")
        sys.exit(1)
