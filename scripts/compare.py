"""
forkprobe main orchestration: run a task in parallel with and without skill(s).

Usage:
    python compare.py --input task.txt --skill baseline --skill humanizer --output report.html

v0.2 status: catalog/BYO skill loading, parallel execution, HTML report
rendering, local verdict capture, and first artifact comparison flows.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

# Make sibling imports work whether script is run as module or directly
sys.path.insert(0, str(Path(__file__).parent))
from platform_adapter import detect_platform, spawn_subagent, SubagentResult
from skill_loader import load_skill, LoadedSkill


# --- Paths ---
SCRIPT_DIR = Path(__file__).parent
PROJECT_DIR = SCRIPT_DIR.parent
CATALOG_DIR = PROJECT_DIR / "catalog"
LOGS_DIR = Path.cwd() / "forkprobe-logs"


# --- Data structures ---

@dataclass
class SkillSpec:
    """One entry in the catalog or a BYO."""
    id: str
    name: str
    author: str
    language: str
    category: str
    source: str  # GitHub URL or local path
    system_prompt: str  # actual prompt to send to subagent


@dataclass
class RunResult:
    """One row in the final report — output of one subagent."""
    skill_id: str
    skill_name: str
    skill_author: str
    skill_category: str
    output: str
    tokens_used: int
    latency_seconds: float
    estimated_tokens_used: int = 0
    provider_tokens_used: int = 0
    token_count_method: str = "estimated_visible_context"
    error: Optional[str] = None


@dataclass
class JudgeResult:
    """AI judge recommendation over all candidate outputs."""
    winner_skill_id: Optional[str]
    verdict_type: str
    confidence: Optional[float]
    summary: str
    reasoning: str
    scores: dict
    tokens_used: int
    latency_seconds: float
    error: Optional[str] = None
    raw_output: str = ""


# --- Catalog loading ---

def load_catalog(domain: str = "academic-writing") -> dict:
    """Load curated skill metadata."""
    catalog_path = CATALOG_DIR / f"{domain}.json"
    if not catalog_path.exists():
        raise FileNotFoundError(f"Catalog not found: {catalog_path}")
    return json.loads(catalog_path.read_text(encoding="utf-8"))


def split_byo_source(source: str) -> tuple[str, Optional[str]]:
    """
    Split BYO skill references of the form "<repo-or-path>#<subdir>".

    This keeps curated catalog entries simple while allowing recommendations to
    point at a specific skill inside a multi-skill repo, for example:
    https://github.com/Yuan1z0825/nature-skills#skills/nature-polishing
    """
    if "#" not in source:
        return source, None
    base, _, fragment = source.partition("#")
    subdir = fragment.strip().strip("/")
    return base, subdir or None


def resolve_skill(skill_id_or_path: str, catalog: dict) -> SkillSpec:
    """
    Resolve a skill ID (catalog lookup) or BYO path/URL to a SkillSpec with system prompt.

    - "baseline" → bare model with generic helpful-assistant prompt
    - catalog ID  → look up source + subdir from catalog, clone if needed, parse SKILL.md
    - URL / path  → BYO: treat as direct source
    """
    if skill_id_or_path == "baseline":
        return SkillSpec(
            id="baseline",
            name="Baseline (no skill)",
            author="—",
            language="—",
            category="baseline",
            source="—",
            system_prompt=(
                "You are a helpful assistant. Complete the user's task to the best of your ability. "
                "Do not apply any specialized skill or framework — just respond naturally."
            ),
        )

    # Check catalog
    for skill_meta in catalog.get("skills", []):
        if skill_meta["id"] == skill_id_or_path:
            loaded: LoadedSkill = load_skill(
                skill_id=skill_meta["id"],
                source=skill_meta["source"],
                subdir=skill_meta.get("subdir"),
            )
            return SkillSpec(
                id=skill_meta["id"],
                name=skill_meta["name"],
                author=skill_meta["author"],
                language=skill_meta["language"],
                category=skill_meta["category"],
                source=skill_meta["source"] + (f"#{skill_meta['subdir']}" if skill_meta.get("subdir") else ""),
                system_prompt=loaded.to_system_prompt(),
            )

    # BYO: GitHub URL or local path
    if skill_id_or_path.startswith(("http://", "https://", "git@", "/", "./", "~/")):
        byo_source, byo_subdir = split_byo_source(skill_id_or_path)
        loaded = load_skill(skill_id="byo", source=byo_source, subdir=byo_subdir)
        return SkillSpec(
            id="byo:" + loaded.name,
            name=loaded.name + " (BYO)",
            author="(user-provided)",
            language="—",
            category="byo",
            source=skill_id_or_path,
            system_prompt=loaded.to_system_prompt(),
        )

    raise KeyError(
        f"Skill {skill_id_or_path!r} not found in catalog and not a recognizable URL/path. "
        f"Available catalog IDs: {[s['id'] for s in catalog.get('skills', [])]}"
    )


# --- Execution ---

def estimate_text_tokens(text: str) -> int:
    """
    Rough token estimate for visible prompt/input/output text.

    We avoid depending on provider-specific tokenizers here because forkprobe can
    run across Claude Code, Codex native CLI, and API fallback paths. The estimate
    intentionally favors stable cross-candidate comparison over provider billing
    precision.
    """
    if not text:
        return 0

    cjk = 0
    latin_like = 0
    other = 0
    for ch in text:
        code = ord(ch)
        if ch.isspace():
            continue
        if (
            0x3400 <= code <= 0x4DBF
            or 0x4E00 <= code <= 0x9FFF
            or 0xF900 <= code <= 0xFAFF
        ):
            cjk += 1
        elif code < 128:
            latin_like += 1
        else:
            other += 1

    return max(1, round(cjk * 0.75 + latin_like / 4 + other / 3))


def estimate_run_tokens(task_input: str, system_prompt: str, output: str) -> int:
    """Estimate visible token load for one candidate run."""
    return estimate_text_tokens(system_prompt) + estimate_text_tokens(task_input) + estimate_text_tokens(output)


def run_one(task_input: str, skill: SkillSpec, platform, timeout: int = 120) -> RunResult:
    """Run a single subagent for one skill."""
    result: SubagentResult = spawn_subagent(
        platform=platform,
        task_input=task_input,
        system_prompt=skill.system_prompt,
        skill_id=skill.id,
        timeout_seconds=timeout,
    )
    return RunResult(
        skill_id=skill.id,
        skill_name=skill.name,
        skill_author=skill.author,
        skill_category=skill.category,
        output=result.output,
        tokens_used=result.tokens_used,
        latency_seconds=result.latency_seconds,
        estimated_tokens_used=estimate_run_tokens(task_input, skill.system_prompt, result.output),
        provider_tokens_used=result.tokens_used,
        error=result.error,
    )


def run_parallel(task_input: str, skills: list[SkillSpec], max_workers: int = 3) -> list[RunResult]:
    """
    Run all skills in parallel and collect results.

    Default max_workers=3 — empirically more reliable than 4-5 for skills with large
    SKILL.md prompts (avoids transient rate-limit / quota errors on bursts).
    Override with FORKPROBE_MAX_WORKERS env var if needed.
    """
    import os
    max_workers = int(os.environ.get("FORKPROBE_MAX_WORKERS", str(max_workers)))
    platform = detect_platform()
    print(f"[forkprobe] Platform: {platform.value}")
    print(f"[forkprobe] Running {len(skills)} paths (concurrency={min(max_workers, len(skills))})...")

    results: list[RunResult] = []
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(run_one, task_input, skill, platform): skill for skill in skills}
        for fut in as_completed(futures):
            skill = futures[fut]
            try:
                result = fut.result()
                status = "✓" if not result.error else "✗"
                print(f"[forkprobe]   {status} {skill.id} ({result.latency_seconds:.1f}s)")
                results.append(result)
            except Exception as e:
                print(f"[forkprobe]   ✗ {skill.id} crashed: {e}")
                results.append(RunResult(
                    skill_id=skill.id, skill_name=skill.name, skill_author=skill.author,
                    skill_category=skill.category, output="", tokens_used=0, latency_seconds=0.0,
                    estimated_tokens_used=estimate_run_tokens(task_input, skill.system_prompt, ""),
                    provider_tokens_used=0,
                    error=str(e),
                ))

    # Preserve input order (baseline first if present)
    order = {s.id: i for i, s in enumerate(skills)}
    results.sort(key=lambda r: order.get(r.skill_id, 999))
    return results


# --- Judge ---

JUDGE_SYSTEM_PROMPT = """You are forkprobe's impartial comparison judge.

Your job is to compare candidate outputs for the same user task. Do not rewrite
the answer. Do not choose based on style alone. Judge which candidate best serves
the user's original task.

Evaluate candidates on:
- fidelity to the user's original intent
- correctness and specificity
- usefulness for the requested domain
- clarity and readability
- for academic writing tasks: natural scholarly tone, reduced AI-like boilerplate,
  and preservation of meaning

Return JSON only, with this exact shape:
{
  "winner_skill_id": "one candidate id, __tie__, or __none__",
  "verdict_type": "pick | tie | none",
  "confidence": 0.0,
  "summary": "one concise sentence",
  "reasoning": "2-5 concise sentences",
  "scores": {
    "candidate_id": {"score": 0, "note": "short note"}
  }
}

Use the same language as the user's task when possible.
"""


def _truncate(text: str, limit: int = 6000) -> str:
    """Keep judge prompts bounded while preserving the start and end of long outputs."""
    if len(text) <= limit:
        return text
    head = text[: limit // 2]
    tail = text[-limit // 2 :]
    return f"{head}\n\n[... truncated {len(text) - limit} chars ...]\n\n{tail}"


def build_judge_task(task_input: str, results: list[RunResult], rubric: Optional[str] = None) -> str:
    """Build the user message sent to the judge subagent."""
    candidate_blocks = []
    for r in results:
        if r.error:
            body = f"[ERROR] {r.error}"
        else:
            body = _truncate(r.output, limit=5000)
        candidate_blocks.append(
            f"## Candidate: {r.skill_id}\n"
            f"Name: {r.skill_name}\n"
            f"Category: {r.skill_category}\n"
            f"Tokens: {r.tokens_used}\n"
            f"Latency: {r.latency_seconds:.1f}s\n\n"
            f"{body}"
        )

    rubric_section = f"\n\n## User rubric\n{rubric}" if rubric else ""
    return (
        "Compare the following forkprobe candidates and recommend a winner.\n\n"
        f"## Original task\n{_truncate(task_input, limit=4000)}"
        f"{rubric_section}\n\n"
        "## Candidates\n\n"
        + "\n\n---\n\n".join(candidate_blocks)
    )


def _extract_json_object(text: str) -> Optional[dict]:
    """Parse a JSON object from model output, tolerating fenced code or preamble."""
    text = text.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


def parse_judge_output(output: str, results: list[RunResult], tokens: int, latency: float) -> JudgeResult:
    """Convert the judge model's JSON into a stable JudgeResult."""
    parsed = _extract_json_object(output)
    valid_ids = {r.skill_id for r in results}
    valid_ids.update({"__tie__", "__none__"})
    if not parsed:
        return JudgeResult(
            winner_skill_id=None,
            verdict_type="none",
            confidence=None,
            summary="Judge returned non-JSON output.",
            reasoning="The raw judge output is included for debugging.",
            scores={},
            tokens_used=tokens,
            latency_seconds=latency,
            error="Could not parse judge JSON.",
            raw_output=output,
        )

    winner = parsed.get("winner_skill_id")
    if winner not in valid_ids:
        winner = None
    verdict_type = parsed.get("verdict_type") or ("tie" if winner == "__tie__" else "none" if winner == "__none__" else "pick")
    if verdict_type not in {"pick", "tie", "none"}:
        verdict_type = "pick" if winner else "none"
    if verdict_type == "tie":
        winner = "__tie__"
    elif verdict_type == "none":
        winner = "__none__"

    confidence = parsed.get("confidence")
    try:
        confidence = float(confidence) if confidence is not None else None
    except (TypeError, ValueError):
        confidence = None
    if confidence is not None:
        confidence = max(0.0, min(1.0, confidence))

    valid_winner = winner in valid_ids

    return JudgeResult(
        winner_skill_id=winner,
        verdict_type=verdict_type,
        confidence=confidence,
        summary=str(parsed.get("summary") or ""),
        reasoning=str(parsed.get("reasoning") or ""),
        scores=parsed.get("scores") if isinstance(parsed.get("scores"), dict) else {},
        tokens_used=tokens,
        latency_seconds=latency,
        error=None if valid_winner else "Judge did not choose a valid winner.",
        raw_output=output,
    )


def run_judge(task_input: str, results: list[RunResult], rubric: Optional[str] = None, timeout: int = 120) -> JudgeResult:
    """Run a judge subagent after candidate generation."""
    platform = detect_platform()
    print(f"[forkprobe] Judge: running on {platform.value}...")
    judge_task = build_judge_task(task_input, results, rubric=rubric)
    result = spawn_subagent(
        platform=platform,
        task_input=judge_task,
        system_prompt=JUDGE_SYSTEM_PROMPT,
        skill_id="__judge__",
        timeout_seconds=timeout,
    )
    if result.error:
        print(f"[forkprobe]   ✗ judge failed: {result.error}")
        return JudgeResult(
            winner_skill_id=None,
            verdict_type="none",
            confidence=None,
            summary="Judge failed.",
            reasoning=result.error,
            scores={},
            tokens_used=result.tokens_used,
            latency_seconds=result.latency_seconds,
            error=result.error,
            raw_output=result.output,
        )
    judge = parse_judge_output(result.output, results, result.tokens_used, result.latency_seconds)
    status = "✓" if not judge.error else "✗"
    print(f"[forkprobe]   {status} judge recommendation: {judge.winner_skill_id or '(none)'} ({judge.latency_seconds:.1f}s)")
    return judge


# --- Logging ---

def write_log(task_input: str, results: list[RunResult], output_path: Path, judge_result: Optional[JudgeResult] = None) -> Path:
    """Append a log entry to forkprobe-logs/. Stores HASH only, never content."""
    LOGS_DIR.mkdir(exist_ok=True)
    timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    run_id = str(uuid.uuid4())[:8]
    log_file = LOGS_DIR / f"{timestamp.replace(':', '')}-{run_id}.json"

    log_entry = {
        "timestamp": timestamp,
        "run_id": run_id,
        "platform": detect_platform().value,
        "task_input_hash": "sha256:" + hashlib.sha256(task_input.encode("utf-8")).hexdigest(),
        "task_input_chars": len(task_input),
        "candidates": [
            {
                "id": r.skill_id,
                "name": r.skill_name,
                "tokens_used": r.tokens_used,
                "provider_tokens_used": r.provider_tokens_used,
                "estimated_tokens_used": r.estimated_tokens_used,
                "token_count_method": r.token_count_method,
                "latency_seconds": r.latency_seconds,
                "had_error": bool(r.error),
            }
            for r in results
        ],
        "judge": asdict(judge_result) if judge_result else None,
        "verdict": None,  # filled in later by HTML report interaction
        "report_path": str(output_path.resolve()),
    }
    log_file.write_text(json.dumps(log_entry, indent=2), encoding="utf-8")
    return log_file


# --- CLI ---

def main():
    parser = argparse.ArgumentParser(
        description="forkprobe: compare a task with and without skills",
    )
    parser.add_argument("--input", required=True, help="Path to task input file")
    parser.add_argument("--skill", action="append", default=[],
                        help="Skill ID (from catalog) or path/URL (BYO). Repeat for multiple. Use 'baseline' for bare model.")
    parser.add_argument("--output", default="./report.html", help="Output HTML report path")
    parser.add_argument("--domain", default="academic-writing", help="Catalog domain (default: academic-writing)")
    parser.add_argument("--no-server", action="store_true",
                        help="Skip the verdict-capture server (report still renders, but verdict goes to browser console only)")
    parser.add_argument("--verdict-timeout", type=int, default=600,
                        help="Seconds to wait for the user to submit a verdict (default: 600 = 10 min)")
    parser.add_argument("--judge", action="store_true",
                        help="Run an extra AI judge after candidate generation and show its recommendation in the report")
    parser.add_argument("--judge-rubric", default=None,
                        help="Optional extra rubric text for --judge (e.g. 'prefer concise Chinese academic prose')")
    parser.add_argument("--judge-timeout", type=int, default=120,
                        help="Seconds to wait for the judge subagent (default: 120)")
    args = parser.parse_args()

    # Load input
    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)
    task_input = input_path.read_text(encoding="utf-8")

    # Ensure baseline is always present
    skill_ids = list(args.skill)
    if "baseline" not in skill_ids:
        skill_ids.insert(0, "baseline")

    # Load catalog and resolve skills
    catalog = load_catalog(args.domain)
    try:
        skills = [resolve_skill(sid, catalog) for sid in skill_ids]
    except (NotImplementedError, KeyError) as e:
        print(f"Error resolving skill: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"[forkprobe] v0.2")
    print(f"[forkprobe] Task input: {len(task_input)} chars from {input_path}")
    print(f"[forkprobe] Skills to compare: {[s.id for s in skills]}")
    print()

    # Run
    t0 = time.time()
    results = run_parallel(task_input, skills)
    candidate_duration = time.time() - t0
    print(f"\n[forkprobe] Done in {candidate_duration:.1f}s")

    judge_result = None
    if args.judge:
        judge_result = run_judge(
            task_input=task_input,
            results=results,
            rubric=args.judge_rubric,
            timeout=args.judge_timeout,
        )
    duration = time.time() - t0

    # Log first so the verdict server can write back to it
    output_path = Path(args.output)
    log_file = write_log(task_input, results, output_path, judge_result=judge_result)
    print(f"[forkprobe] Log: {log_file}")

    # Start verdict-capture server (D4)
    verdict_url = None
    stop_verdict_server = None
    if not args.no_server:
        try:
            from verdict_server import build_verdict_url, start_server, wait_for_verdict, stop_server
            port = start_server(log_file)
            verdict_url = build_verdict_url(port)
            stop_verdict_server = stop_server
            print(f"[forkprobe] Verdict server: loopback-only endpoint ready on port {port}")
        except Exception as e:
            print(f"[forkprobe] Could not start verdict server ({e}). Continuing without it.")

    # Render report
    try:
        from render_report import render
        render(
            task_input=task_input,
            results=[asdict(r) for r in results],
            duration_seconds=duration,
            output_path=output_path,
            verdict_url=verdict_url,
            judge_result=asdict(judge_result) if judge_result else None,
        )
        print(f"[forkprobe] Report: {output_path.resolve()}")
    except (ImportError, RuntimeError) as e:
        print(f"[forkprobe] Report rendering failed: {e}", file=sys.stderr)
        if stop_verdict_server:
            stop_verdict_server()
        sys.exit(1)

    # Wait for the user's verdict (or timeout)
    if verdict_url:
        try:
            print(f"[forkprobe] Waiting up to {args.verdict_timeout}s for your verdict in the browser...")
            verdict = wait_for_verdict(timeout_seconds=args.verdict_timeout)
            if verdict:
                print(f"[forkprobe] ✓ Verdict captured: winner={verdict.get('winner')}, type={verdict.get('verdict_type')}")
                handoff_text = verdict.get("handoff_text")
                if handoff_text:
                    print("\n[forkprobe] Continuation handoff (copy back into your agent session if it does not continue automatically):")
                    print(handoff_text)
                if verdict.get("handoff_path"):
                    print(f"[forkprobe] Handoff file: {verdict.get('handoff_path')}")
            else:
                print(f"[forkprobe] No verdict received before timeout. Log retained anyway.")
        finally:
            if stop_verdict_server:
                stop_verdict_server()


if __name__ == "__main__":
    main()
