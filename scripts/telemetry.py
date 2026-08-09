"""Privacy-preserving community selection telemetry for ForkProbe.

Reports post verdicts to the loopback verdict server. This module extracts the
small, explicitly allowed selection event, stores it in a local outbox, and
optionally sends it to the configured ForkProbe telemetry endpoint.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any


FORKPROBE_HOME = Path(os.environ.get("FORKPROBE_HOME", Path.home() / ".forkprobe")).expanduser()
CONFIG_PATH = FORKPROBE_HOME / "config.json"
OUTBOX_DIR = FORKPROBE_HOME / "telemetry" / "outbox"
CONSENT_VERSION = 1
EVENT_SCHEMA_VERSION = 1
MAX_CANDIDATES = 10
MAX_NAME_LENGTH = 128
_TASK_TYPE_RE = re.compile(r"^[a-z0-9][a-z0-9_:-]{0,63}$")
_FALSE_VALUES = {"0", "false", "no", "off"}


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def load_config(path: Path | None = None) -> dict[str, Any]:
    target = path or CONFIG_PATH
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def sharing_default(path: Path | None = None) -> bool:
    """Return the report checkbox default; first use is intentionally checked."""
    env_value = os.environ.get("FORKPROBE_TELEMETRY")
    if env_value is not None:
        return env_value.lower() not in _FALSE_VALUES
    sharing = load_config(path).get("anonymous_selection_sharing")
    if isinstance(sharing, dict) and isinstance(sharing.get("enabled"), bool):
        return bool(sharing["enabled"])
    return True


def sharing_forced_off() -> bool:
    """Return True when the environment explicitly disables all telemetry."""
    env_value = os.environ.get("FORKPROBE_TELEMETRY")
    return env_value is not None and env_value.strip().lower() in _FALSE_VALUES


def sharing_allowed(requested: bool) -> bool:
    """Apply the process-level privacy override to a report's explicit choice."""
    return bool(requested) and not sharing_forced_off()


def save_sharing_preference(enabled: bool, path: Path | None = None) -> dict[str, Any]:
    target = path or CONFIG_PATH
    config = load_config(target)
    config["anonymous_selection_sharing"] = {
        "enabled": bool(enabled),
        "consent_version": CONSENT_VERSION,
        "confirmed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    _atomic_write_json(target, config)
    return config


def _clean_name(value: Any) -> str:
    return " ".join(str(value or "").split())[:MAX_NAME_LENGTH]


def _candidate_names(log: dict[str, Any]) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for candidate in log.get("candidates", []):
        if not isinstance(candidate, dict):
            continue
        name = _clean_name(candidate.get("name") or candidate.get("skill_name") or candidate.get("id"))
        key = name.casefold()
        if not name or key in seen:
            continue
        seen.add(key)
        names.append(name)
        if len(names) >= MAX_CANDIDATES:
            break
    return names


def _winner_name(log: dict[str, Any], verdict: dict[str, Any], candidate_names: list[str]) -> str:
    winner = _clean_name(verdict.get("winner"))
    for candidate in log.get("candidates", []):
        if not isinstance(candidate, dict):
            continue
        if _clean_name(candidate.get("id")) == winner:
            return _clean_name(candidate.get("name") or candidate.get("skill_name") or winner)

    requested_name = _clean_name(verdict.get("winner_name"))
    known_by_casefold = {name.casefold(): name for name in candidate_names}
    return known_by_casefold.get(requested_name.casefold(), "__none__")


def build_selection_event(log: dict[str, Any], verdict: dict[str, Any]) -> dict[str, Any]:
    """Build the exact anonymous payload allowed to leave the local machine."""
    task_type = str(log.get("task_type") or "unknown").strip().lower()
    if not _TASK_TYPE_RE.fullmatch(task_type):
        task_type = "unknown"
    candidate_names = _candidate_names(log)
    verdict_type = str(verdict.get("verdict_type") or "pick")
    winner = str(verdict.get("winner") or "")
    if verdict_type == "tie" or winner == "__tie__":
        final_choice = "__tie__"
    elif verdict_type == "none" or winner == "__none__":
        final_choice = "__none__"
    else:
        final_choice = _winner_name(log, verdict, candidate_names)
    return {
        "schema_version": EVENT_SCHEMA_VERSION,
        "event_id": str(uuid.uuid4()),
        "task_type": task_type,
        "candidate_skill_names": candidate_names,
        "final_choice": final_choice,
    }


def enqueue_event(event: dict[str, Any], outbox_dir: Path | None = None) -> Path:
    target_dir = outbox_dir or OUTBOX_DIR
    target = target_dir / f"{event['event_id']}.json"
    _atomic_write_json(target, event)
    return target


def telemetry_endpoint() -> str:
    return os.environ.get("FORKPROBE_TELEMETRY_ENDPOINT", "").strip()


def send_event(event: dict[str, Any], endpoint: str | None = None, timeout: float = 2.0) -> None:
    target = (endpoint if endpoint is not None else telemetry_endpoint()).strip()
    if not target:
        raise ValueError("ForkProbe telemetry endpoint is not configured")
    request = urllib.request.Request(
        target,
        data=json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "forkprobe-selection-telemetry"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        if not 200 <= int(response.status) < 300:
            raise urllib.error.HTTPError(target, response.status, "telemetry rejected", response.headers, None)


def flush_outbox(
    outbox_dir: Path | None = None,
    endpoint: str | None = None,
    timeout: float = 2.0,
    max_events: int = 10,
) -> dict[str, int]:
    target_dir = outbox_dir or OUTBOX_DIR
    sent = 0
    failed = 0
    if not target_dir.exists() or not (endpoint if endpoint is not None else telemetry_endpoint()).strip():
        return {"sent": sent, "failed": failed}
    for path in sorted(target_dir.glob("*.json"))[:max_events]:
        try:
            event = json.loads(path.read_text(encoding="utf-8"))
            send_event(event, endpoint=endpoint, timeout=timeout)
            path.unlink()
            sent += 1
        except (OSError, ValueError, json.JSONDecodeError, urllib.error.URLError, urllib.error.HTTPError, TimeoutError):
            failed += 1
    return {"sent": sent, "failed": failed}


def enqueue_and_flush_async(log: dict[str, Any], verdict: dict[str, Any]) -> Path:
    event = build_selection_event(log, verdict)
    queued_path = enqueue_event(event)
    thread = threading.Thread(target=flush_outbox, kwargs={"max_events": 5}, daemon=True)
    thread.start()
    return queued_path


def flush_outbox_async(max_events: int = 10) -> threading.Thread:
    """Retry queued events without delaying report generation or continuation."""
    thread = threading.Thread(target=flush_outbox, kwargs={"max_events": max_events}, daemon=True)
    thread.start()
    return thread
