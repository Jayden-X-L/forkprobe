"""
Resume from the latest forkprobe verdict.

This is the chat-side bridge: when a user says "I picked one" after selecting a
winner in report.html, the active agent can run this script to recover the
winner and continuation handoff from local forkprobe logs.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent


def _default_logs_dirs() -> list[Path]:
    dirs = [Path.cwd() / "forkprobe-logs", PROJECT_DIR / "forkprobe-logs"]
    deduped: list[Path] = []
    seen: set[Path] = set()
    for path in dirs:
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            deduped.append(resolved)
    return deduped


def _read_json(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _has_verdict(log: Optional[dict]) -> bool:
    return bool(log and isinstance(log.get("verdict"), dict) and log["verdict"].get("winner"))


def _candidate_name(log: dict, winner: str) -> str:
    verdict = log.get("verdict") or {}
    winner_name = verdict.get("winner_name")
    if winner_name:
        return str(winner_name)
    for candidate in log.get("candidates", []):
        if candidate.get("id") == winner:
            return str(candidate.get("name") or winner)
    return winner


def find_latest_verdict_log(logs_dirs: Optional[list[Path]] = None, log_path: Optional[Path] = None) -> tuple[Optional[Path], Optional[dict]]:
    """Return the newest log containing a submitted verdict."""
    if log_path:
        resolved = log_path.resolve()
        log = _read_json(resolved)
        return (resolved, log) if _has_verdict(log) else (resolved, log)

    search_dirs = logs_dirs or _default_logs_dirs()
    candidates: list[Path] = []
    for logs_dir in search_dirs:
        latest = logs_dir / "latest.json"
        if latest.exists():
            candidates.append(latest)
        if logs_dir.exists():
            candidates.extend(
                sorted(
                    (p for p in logs_dir.glob("*.json") if p.name != "latest.json"),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                )
            )

    seen: set[Path] = set()
    for path in candidates:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        log = _read_json(resolved)
        if _has_verdict(log):
            return resolved, log
    return (None, None)


def build_resume_payload(log_path: Path, log: dict) -> dict:
    verdict = dict(log.get("verdict") or {})
    winner = str(verdict.get("winner") or "")
    winner_name = _candidate_name(log, winner)
    handoff_text = str(verdict.get("handoff_text") or "").strip()
    handoff_path = verdict.get("handoff_path") or log.get("handoff_path") or log.get("latest_handoff_path")

    return {
        "status": "ok",
        "log_path": str(log_path.resolve()),
        "source_log_path": log.get("source_log_path") or str(log_path.resolve()),
        "report_path": log.get("report_path"),
        "handoff_path": handoff_path,
        "winner": winner,
        "winner_name": winner_name,
        "verdict_type": verdict.get("verdict_type") or "pick",
        "reason": verdict.get("reason") or "",
        "handoff_text": handoff_text,
        "verdict_received_at": log.get("verdict_received_at"),
    }


def format_payload(payload: dict) -> str:
    lines = [
        "forkprobe verdict found",
        f"Winner: {payload['winner_name']} ({payload['winner']})",
        f"Verdict type: {payload['verdict_type']}",
    ]
    if payload.get("reason"):
        lines.append(f"Reason: {payload['reason']}")
    if payload.get("report_path"):
        lines.append(f"Report: {payload['report_path']}")
    if payload.get("handoff_path"):
        lines.append(f"Handoff file: {payload['handoff_path']}")
    if payload.get("handoff_text"):
        lines.extend(["", "Continuation handoff:", payload["handoff_text"]])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Resume from the latest forkprobe verdict")
    parser.add_argument("--latest", action="store_true", help="Read latest verdict from forkprobe-logs (default)")
    parser.add_argument("--log", help="Specific forkprobe log JSON to inspect")
    parser.add_argument("--logs-dir", action="append", help="Directory containing forkprobe logs; repeatable")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    args = parser.parse_args()

    logs_dirs = [Path(p).resolve() for p in args.logs_dir] if args.logs_dir else None
    log_path = Path(args.log).resolve() if args.log else None
    found_path, log = find_latest_verdict_log(logs_dirs=logs_dirs, log_path=log_path)

    if not found_path or not _has_verdict(log):
        payload = {
            "status": "no_verdict",
            "log_path": str(found_path) if found_path else None,
            "message": (
                "No submitted forkprobe verdict was found. The report may still be in demo mode, "
                "the user may have clicked Pick without Submit, or the verdict server may have timed out."
            ),
        }
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(payload["message"], file=sys.stderr)
        return 2

    payload = build_resume_payload(found_path, log or {})
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(format_payload(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
