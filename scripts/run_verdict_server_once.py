from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from verdict_server import start_server, stop_server, wait_for_verdict


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a forkprobe verdict server for one report.")
    parser.add_argument("--log", required=True, help="forkprobe log JSON to update")
    parser.add_argument("--port-file", required=True, help="file where the selected port is written")
    parser.add_argument("--timeout", type=int, default=1800, help="seconds to wait for a verdict")
    args = parser.parse_args()

    log_path = Path(args.log).resolve()
    port_file = Path(args.port_file).resolve()
    port = start_server(log_path)
    port_file.write_text(str(port), encoding="utf-8")
    print(f"Verdict server: http://127.0.0.1:{port}/verdict", flush=True)

    try:
        verdict = wait_for_verdict(timeout_seconds=args.timeout)
        print(f"Verdict: {verdict}", flush=True)
    finally:
        stop_server()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
