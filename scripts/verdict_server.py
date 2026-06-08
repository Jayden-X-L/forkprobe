"""
Tiny local HTTP server that captures the user's verdict from report.html.

Flow:
  1. compare.py calls start_server(log_path) -> port
  2. report.html.j2 receives a tokenized local verdict endpoint
  3. Browser opens report.html; user clicks "Pick this one" + submits
  4. JS posts the verdict JSON back to the loopback-only endpoint
  5. Server writes verdict back into the log JSON, then shuts down
  6. compare.py's wait_for_verdict() unblocks and prints success

Privacy: server binds to 127.0.0.1 only. Never listens on external interfaces.
"""
from __future__ import annotations

import json
import secrets
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlencode, urlsplit


# Module-level state for the running server
_server_thread: Optional[threading.Thread] = None
_httpd: Optional[HTTPServer] = None
_verdict_event = threading.Event()
_log_path_holder: dict = {"path": None}
_received_verdict: dict = {}
_server_token: Optional[str] = None

_BIND_HOST = "127.0.0.1"
_URL_HOST = "localhost"
_ALLOWED_ORIGIN_HOSTS = {_URL_HOST, _BIND_HOST, "::1"}


def build_verdict_url(port: int, token: Optional[str] = None) -> str:
    """Return the tokenized local endpoint used by the generated report."""
    active_token = token if token is not None else _server_token
    query = f"?{urlencode({'token': active_token})}" if active_token else ""
    return f"http://{_URL_HOST}:{port}/verdict{query}"


def _origin_is_allowed(origin: Optional[str]) -> bool:
    """Allow file:// reports and loopback browser origins only."""
    if not origin or origin == "null":
        return True
    try:
        parsed = urlsplit(origin)
    except ValueError:
        return False
    return (parsed.hostname or "") in _ALLOWED_ORIGIN_HOSTS


def build_handoff_text(verdict: dict, log: Optional[dict] = None) -> str:
    """Return a short continuation prompt the user can paste into an agent chat."""
    existing = str(verdict.get("handoff_text") or "").strip()
    if existing:
        return existing

    verdict_type = verdict.get("verdict_type") or "pick"
    winner = verdict.get("winner") or ""
    winner_name = verdict.get("winner_name") or winner
    reason = str(verdict.get("reason") or "").strip() or "No extra reason provided."

    if verdict_type == "tie" or winner == "__tie__":
        return (
            "I think these forkprobe outputs are roughly tied. Please continue by combining "
            "the strongest parts, or ask me which direction to prefer.\n"
            f"My reason: {reason}"
        )
    if verdict_type == "none" or winner == "__none__":
        return (
            "I am not satisfied with any forkprobe output. Please retry with a different "
            "approach or ask me what should change.\n"
            f"My reason: {reason}"
        )

    if not winner_name and log:
        for candidate in log.get("candidates", []):
            if candidate.get("id") == winner:
                winner_name = candidate.get("name") or winner
                break

    return (
        f"Please continue this task using {winner_name or winner} ({winner}) for the rest of this task.\n"
        f"My reason: {reason}"
    )


def write_handoff_file(log_path: Path, log: dict, verdict: dict) -> Path:
    """Write a privacy-preserving continuation handoff beside the verdict log."""
    handoff_text = build_handoff_text(verdict, log=log)
    verdict["handoff_text"] = handoff_text

    winner = verdict.get("winner") or "(unknown)"
    winner_name = verdict.get("winner_name") or winner
    verdict_type = verdict.get("verdict_type") or "pick"
    reason = str(verdict.get("reason") or "").strip() or "(none)"
    report_path = log.get("report_path") or "(unknown)"
    handoff_path = log_path.with_name(f"{log_path.stem}.handoff.md")

    body = (
        "# forkprobe Continuation Handoff\n\n"
        f"Winner: {winner_name} ({winner})\n\n"
        f"Verdict type: {verdict_type}\n\n"
        f"Reason: {reason}\n\n"
        f"Report: {report_path}\n\n"
        "## Copy back into your agent session\n\n"
        f"{handoff_text}\n"
    )
    handoff_path.write_text(body, encoding="utf-8")
    return handoff_path


def write_latest_files(log_path: Path, log: dict, handoff_path: Optional[Path] = None) -> tuple[Path, Optional[Path]]:
    """Write stable latest pointers so an agent can resume after the browser is closed."""
    logs_dir = log_path.parent
    latest_log_path = logs_dir / "latest.json"
    latest_handoff_path = logs_dir / "latest.handoff.md"

    log["source_log_path"] = str(log_path.resolve())
    log["latest_pointer_updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    latest_log_path.write_text(json.dumps(log, indent=2, ensure_ascii=False), encoding="utf-8")

    written_handoff = None
    if handoff_path and handoff_path.exists():
        latest_handoff_path.write_text(handoff_path.read_text(encoding="utf-8"), encoding="utf-8")
        written_handoff = latest_handoff_path
    return latest_log_path, written_handoff


def _find_free_port() -> int:
    """Pick an available high port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((_BIND_HOST, 0))
        return s.getsockname()[1]


class _VerdictHandler(BaseHTTPRequestHandler):
    """Handles POST /verdict and (optionally) GET / for liveness."""

    def _send_cors(self) -> bool:
        origin = self.headers.get("Origin")
        if not _origin_is_allowed(origin):
            return False
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        return True

    def do_OPTIONS(self):  # CORS preflight (file:// pages need this)
        if not _origin_is_allowed(self.headers.get("Origin")):
            self.send_response(403)
            self.end_headers()
            return
        self.send_response(204)
        self._send_cors()
        self.end_headers()

    def do_GET(self):
        if self.path == "/" or self.path == "/health":
            self.send_response(200)
            self._send_cors()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ready"}')
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        parsed_path = urlsplit(self.path)
        if parsed_path.path != "/verdict":
            self.send_response(404)
            self.end_headers()
            return

        if not _origin_is_allowed(self.headers.get("Origin")):
            self.send_response(403)
            self.end_headers()
            return

        query = parse_qs(parsed_path.query)
        supplied_token = (query.get("token") or [""])[0]
        if _server_token and supplied_token != _server_token:
            self.send_response(403)
            self._send_cors()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": "invalid verdict token"}).encode("utf-8"))
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            body_bytes = self.rfile.read(length)
            verdict = json.loads(body_bytes.decode("utf-8"))
        except (ValueError, json.JSONDecodeError) as e:
            self.send_response(400)
            self._send_cors()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
            return

        verdict["handoff_text"] = build_handoff_text(verdict)
        response = {"ok": True}

        # Persist verdict into the log file (if available)
        log_path = _log_path_holder.get("path")
        if log_path and log_path.exists():
            try:
                log = json.loads(log_path.read_text(encoding="utf-8"))
                handoff_path = write_handoff_file(log_path, log, verdict)
                verdict["handoff_path"] = str(handoff_path.resolve())
                log["verdict"] = verdict
                log["verdict_received_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                log["handoff_path"] = verdict["handoff_path"]
                log_path.write_text(json.dumps(log, indent=2, ensure_ascii=False), encoding="utf-8")
                latest_log_path, latest_handoff_path = write_latest_files(log_path, log, handoff_path)
                log["latest_log_path"] = str(latest_log_path.resolve())
                if latest_handoff_path:
                    log["latest_handoff_path"] = str(latest_handoff_path.resolve())
                    verdict["latest_handoff_path"] = log["latest_handoff_path"]
                log_path.write_text(json.dumps(log, indent=2, ensure_ascii=False), encoding="utf-8")
                latest_log_path.write_text(json.dumps(log, indent=2, ensure_ascii=False), encoding="utf-8")
                response["handoff_path"] = verdict["handoff_path"]
                response["latest_log_path"] = log["latest_log_path"]
            except Exception as e:
                # Non-fatal — still record in memory
                print(f"[verdict_server] could not update log: {e}")

        _received_verdict.update(verdict)
        _verdict_event.set()

        self.send_response(200)
        self._send_cors()
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(response).encode("utf-8"))

    def log_message(self, fmt, *args):  # silence default access log
        return


def start_server(log_path: Path, token: Optional[str] = None) -> int:
    """
    Start the verdict server on a free localhost port. Returns the port.

    Idempotent — calling twice in the same process reuses the same server.
    """
    global _server_thread, _httpd, _server_token

    _log_path_holder["path"] = log_path
    _verdict_event.clear()
    _received_verdict.clear()
    _server_token = token or secrets.token_urlsafe(24)

    if _httpd is not None:
        # Already running, just rebind log path
        return _httpd.server_address[1]

    port = _find_free_port()
    _httpd = HTTPServer((_BIND_HOST, port), _VerdictHandler)
    _server_thread = threading.Thread(target=_httpd.serve_forever, daemon=True)
    _server_thread.start()
    return port


def wait_for_verdict(timeout_seconds: int = 600) -> Optional[dict]:
    """
    Block until a verdict is POSTed, or timeout expires. Returns the verdict dict
    or None on timeout.
    """
    if _verdict_event.wait(timeout=timeout_seconds):
        return dict(_received_verdict)
    return None


def stop_server() -> None:
    global _httpd, _server_thread, _server_token
    if _httpd is not None:
        _httpd.shutdown()
        _httpd.server_close()
        _httpd = None
        _server_thread = None
        _server_token = None


# --- Smoke test ---

if __name__ == "__main__":
    import tempfile

    tmp_log = Path(tempfile.mkstemp(suffix=".json")[1])
    tmp_log.write_text(json.dumps({"timestamp": "test", "candidates": [], "verdict": None}, indent=2))

    port = start_server(tmp_log)
    print(f"Verdict server listening on a loopback-only endpoint: {build_verdict_url(port)}")
    print(f"Log file: {tmp_log}")
    print("Submit a JSON verdict with any local HTTP client, including the generated token.")
    print("Waiting up to 60s for a verdict...")

    verdict = wait_for_verdict(timeout_seconds=60)
    if verdict:
        print(f"Got verdict: {verdict}")
        print(f"Log now:\n{tmp_log.read_text()}")
    else:
        print("Timed out.")

    stop_server()
    tmp_log.unlink()
