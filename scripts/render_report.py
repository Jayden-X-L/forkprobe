"""
Render the comparison report as HTML.
"""
from __future__ import annotations

import json
import os
import sys
import webbrowser
from pathlib import Path
from typing import Optional

SCRIPT_DIR = Path(__file__).parent
PROJECT_DIR = SCRIPT_DIR.parent
TEMPLATES_DIR = PROJECT_DIR / "templates"


def _candidate_dependency_paths() -> list[Path]:
    """Find likely local site-packages dirs when the active Python misses jinja2."""
    raw_env = os.environ.get("FORKPROBE_PYTHONPATH", "")
    candidates = [Path(p).expanduser() for p in raw_env.split(os.pathsep) if p]
    search_roots = [PROJECT_DIR, PROJECT_DIR.parent, Path.cwd()]
    for root in search_roots:
        candidates.extend(root.glob(".venv/lib/python*/site-packages"))
        candidates.extend(root.glob(".venv/lib/python*/dist-packages"))
        candidates.extend(root.glob("venv/lib/python*/site-packages"))
        candidates.extend(root.glob("venv/lib/python*/dist-packages"))
    seen: set[Path] = set()
    existing = []
    for path in candidates:
        resolved = path.resolve()
        if resolved.exists() and resolved not in seen:
            seen.add(resolved)
            existing.append(resolved)
    return existing


def _load_template_class():
    """
    Load jinja2.Template for the modern report template.

    forkprobe used to silently fall back to a simple HTML report when jinja2 was
    unavailable. That made successful runs look like a different product. Now we
    try common local dependency paths, then fail loudly with a setup hint.
    """
    try:
        from jinja2 import Template
        return Template
    except ImportError as first_error:
        for path in _candidate_dependency_paths():
            path_str = str(path)
            if path_str not in sys.path:
                sys.path.insert(0, path_str)
        try:
            from jinja2 import Template
            return Template
        except ImportError as second_error:
            raise RuntimeError(
                "forkprobe could not render the modern report because jinja2 is not available "
                "to this Python process. Install it with `python3 -m pip install jinja2`, "
                "or set FORKPROBE_PYTHONPATH to a site-packages directory that contains jinja2. "
                "No fallback HTML was written, so the report UI cannot silently downgrade."
            ) from second_error or first_error


def _as_int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def render(
    task_input: str,
    results: list[dict],
    duration_seconds: float,
    output_path: Path,
    auto_open: bool = True,
    verdict_url: Optional[str] = None,
    judge_result: Optional[dict] = None,
) -> Path:
    """
    Render the comparison report as a self-contained HTML file.

    Args:
        task_input: original user input (will be embedded; in-memory only, no disk re-leak beyond report.html)
        results: list of dicts from RunResult.__dict__
        duration_seconds: total elapsed time
        output_path: where to write report.html
        auto_open: if True, open in default browser after writing
        verdict_url: optional http://127.0.0.1:PORT/verdict URL; when provided,
                     the report POSTs verdict data back to it
        judge_result: optional dict from compare.py's JudgeResult

    Returns:
        Path to written report
    """
    candidates_by_id = {
        r.get("skill_id"): {
            "name": r.get("skill_name"),
            "author": r.get("skill_author"),
            "category": r.get("skill_category"),
        }
        for r in results
        if r.get("skill_id")
    }
    total_estimated_tokens = sum(
        _as_int(r.get("estimated_tokens_used") or r.get("tokens_used"))
        for r in results
    )

    template_path = TEMPLATES_DIR / "report.html.j2"
    if not template_path.exists():
        raise RuntimeError(
            f"forkprobe modern report template not found: {template_path}. "
            "Run compare.py from a complete forkprobe checkout that includes templates/report.html.j2."
        )

    Template = _load_template_class()
    template_source = template_path.read_text(encoding="utf-8")
    template = Template(template_source)
    html_output = template.render(
        task_input=task_input,
        results=results,
        duration_seconds=duration_seconds,
        result_count=len(results),
        total_estimated_tokens=total_estimated_tokens,
        total_estimated_tokens_display=f"{total_estimated_tokens:,}",
        verdict_url=verdict_url or "",
        judge_result=judge_result,
        candidates_json=json.dumps(candidates_by_id, ensure_ascii=False).replace("</", "<\\/"),
    )

    output_path.write_text(html_output, encoding="utf-8")

    if auto_open:
        try:
            webbrowser.open(f"file://{output_path.resolve()}")
        except Exception as e:
            # Non-fatal — user can open manually
            print(f"[forkprobe] Could not auto-open browser: {e}")
            print(f"[forkprobe] Open manually: {output_path.resolve()}")

    return output_path


if __name__ == "__main__":
    # Sanity check — render with dummy data
    import sys
    dummy_results = [
        {
            "skill_id": "baseline",
            "skill_name": "Baseline (no skill)",
            "skill_author": "—",
            "skill_category": "baseline",
            "output": "This is what the bare model would produce. Lorem ipsum dolor sit amet, consectetur adipiscing elit.",
            "tokens_used": 480,
            "latency_seconds": 3.2,
            "error": None,
        },
        {
            "skill_id": "humanizer",
            "skill_name": "humanizer",
            "skill_author": "blader",
            "skill_category": "anti-AI",
            "output": "This is what humanizer would produce. Different word choices, less robotic phrasing.",
            "tokens_used": 620,
            "latency_seconds": 4.1,
            "error": None,
        },
    ]
    out = Path("./report-test.html")
    render(
        task_input="A sample input paragraph for testing the report rendering.",
        results=dummy_results,
        duration_seconds=4.5,
        output_path=out,
        auto_open=False,
        judge_result={
            "winner_skill_id": "humanizer",
            "verdict_type": "pick",
            "confidence": 0.74,
            "summary": "humanizer is more natural.",
            "reasoning": "It keeps the meaning while making the prose less robotic.",
            "scores": {"baseline": {"score": 70, "note": "clear"}, "humanizer": {"score": 84, "note": "more natural"}},
            "tokens_used": 120,
            "latency_seconds": 2.1,
            "error": None,
            "raw_output": "",
        },
    )
    print(f"Test report: {out.resolve()}")
