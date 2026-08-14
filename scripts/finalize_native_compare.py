"""Finalize native DSH subagent results into ForkProbe's Report workflow."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path

from compare import (
    JudgeResult,
    RunResult,
    estimate_run_tokens,
    parse_judge_output,
    text_constraint_warnings,
    write_log,
)
from render_report import open_report, render
from verdict_server import build_verdict_url


def _start_verdict_process(log_file: Path, timeout_seconds: int) -> tuple[str | None, int | None]:
    run_id = log_file.stem
    port_file = log_file.parent / f".{run_id}.port"
    server_log = log_file.parent / f"{run_id}.verdict-server.log"
    port_file.unlink(missing_ok=True)
    with server_log.open("ab") as stream:
        process = subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).with_name("run_verdict_server_once.py")),
                "--log",
                str(log_file),
                "--port-file",
                str(port_file),
                "--timeout",
                str(timeout_seconds),
            ],
            stdin=subprocess.DEVNULL,
            stdout=stream,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )

    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return None, None
        if port_file.exists():
            try:
                endpoint = port_file.read_text(encoding="utf-8").strip()
                if endpoint.startswith("http://localhost:"):
                    return endpoint, process.pid
                # Backward compatibility with an older helper that wrote only a port.
                return build_verdict_url(int(endpoint)), process.pid
            except (OSError, ValueError):
                pass
        time.sleep(0.1)
    process.terminate()
    return None, None


def _run_result(task_input: str, value: dict) -> RunResult:
    output = str(value.get("output") or "")
    system_prompt = str(value.get("system_prompt") or "")
    token_method = str(value.get("token_count_method") or "estimated_visible_context")
    provider_tokens = int(value.get("provider_tokens_used") or 0) if token_method == "provider_reported" else 0
    estimated_tokens = int(value.get("estimated_tokens_used") or 0) or estimate_run_tokens(
        task_input,
        system_prompt,
        output,
    )
    return RunResult(
        skill_id=str(value.get("skill_id") or "unknown"),
        skill_name=str(value.get("skill_name") or value.get("skill_id") or "Unknown"),
        skill_author=str(value.get("skill_author") or ""),
        skill_category=str(value.get("skill_category") or ""),
        output=output,
        tokens_used=provider_tokens,
        latency_seconds=float(value.get("latency_seconds") or 0),
        estimated_tokens_used=estimated_tokens,
        provider_tokens_used=provider_tokens,
        token_count_method=token_method,
        qa_warnings=text_constraint_warnings(task_input, output),
        error=str(value.get("error")) if value.get("error") else None,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Render a ForkProbe report from native DSH subagent results")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--verdict-timeout", type=int, default=1800)
    parser.add_argument("--no-server", action="store_true")
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args()

    manifest_path = Path(args.manifest).resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1:
        raise ValueError("unsupported native DSH manifest schema")

    task_input = str(manifest.get("task_input") or "")
    results = [_run_result(task_input, value) for value in manifest.get("results") or []]
    if len(results) < 2:
        raise ValueError("native DSH manifest must contain at least two candidates")

    judge_result: JudgeResult | None = None
    judge = manifest.get("judge")
    if isinstance(judge, dict) and judge.get("enabled"):
        if judge.get("error"):
            judge_result = JudgeResult(
                winner_skill_id=None,
                verdict_type="none",
                confidence=None,
                summary="Judge failed.",
                reasoning=str(judge.get("error")),
                scores={},
                tokens_used=0,
                latency_seconds=float(judge.get("latency_seconds") or 0),
                error=str(judge.get("error")),
                raw_output=str(judge.get("output") or ""),
            )
        else:
            judge_result = parse_judge_output(
                str(judge.get("output") or ""),
                results,
                0,
                float(judge.get("latency_seconds") or 0),
            )

    output_path = Path(args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    workspace = Path(args.workspace).resolve()
    logs_dir = workspace / "forkprobe-logs"
    log_file = write_log(
        task_input,
        results,
        output_path,
        judge_result=judge_result,
        task_type=str(manifest.get("task_type") or "text_general"),
        platform_name="deepseek_harness_native",
        logs_dir=logs_dir,
    )

    local_bundle = output_path.with_suffix(".forkprobe-run.json")
    local_bundle.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    log = json.loads(log_file.read_text(encoding="utf-8"))
    log["execution_mode"] = "native_dsh_plugin"
    log["local_result_bundle_path"] = str(local_bundle)
    log_file.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")

    verdict_url = None
    server_pid = None
    if not args.no_server:
        verdict_url, server_pid = _start_verdict_process(log_file, args.verdict_timeout)

    render(
        task_input=task_input,
        results=[asdict(result) for result in results],
        duration_seconds=float(manifest.get("duration_seconds") or 0),
        output_path=output_path,
        auto_open=False,
        verdict_url=verdict_url,
        judge_result=asdict(judge_result) if judge_result else None,
    )
    opened = False if args.no_open else open_report(output_path)

    response = {
        "status": "ready",
        "report_path": str(output_path),
        "log_path": str(log_file),
        "local_result_bundle_path": str(local_bundle),
        "verdict_connected": bool(verdict_url),
        "verdict_server_pid": server_pid,
        "report_opened": opened,
        "judge_winner": judge_result.winner_skill_id if judge_result else None,
        "candidate_count": len(results),
    }
    print(json.dumps(response, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
