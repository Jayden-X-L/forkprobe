"""Resolve ForkProbe Skill references for a native DSH plugin run."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from compare import dedupe_skill_specs, load_catalog, resolve_skill, skill_prompt_fingerprint


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare Skill prompts for ForkProbe's native DSH plugin")
    parser.add_argument("--skills-json", required=True, help="JSON file containing a list of Skill IDs, paths, or URLs")
    parser.add_argument("--domain", default="academic-writing")
    parser.add_argument("--output", required=True)
    parser.add_argument("--max-candidates", type=int, default=5)
    args = parser.parse_args()

    raw = json.loads(Path(args.skills_json).read_text(encoding="utf-8"))
    if not isinstance(raw, list) or not all(isinstance(value, str) and value.strip() for value in raw):
        raise ValueError("skills JSON must be a non-empty list of Skill IDs, paths, or HTTPS URLs")

    skill_ids = [value.strip() for value in raw]
    if "baseline" not in skill_ids:
        skill_ids.insert(0, "baseline")
    if len(skill_ids) > args.max_candidates * 2:
        raise ValueError("too many unresolved Skill references")

    catalog = load_catalog(args.domain)
    resolved = dedupe_skill_specs([resolve_skill(value, catalog) for value in skill_ids])
    if len(resolved) > args.max_candidates:
        raise ValueError(f"resolved candidate count exceeds the configured maximum of {args.max_candidates}")
    if len(resolved) < 2:
        raise ValueError("ForkProbe needs at least two distinct candidates after deduplication")

    payload = {
        "schema_version": 1,
        "skills": [
            {
                "id": skill.id,
                "name": skill.name,
                "author": skill.author,
                "category": skill.category,
                "source": skill.source,
                "system_prompt": skill.system_prompt,
                "fingerprint": skill_prompt_fingerprint(skill.system_prompt),
            }
            for skill in resolved
        ],
    }
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
