"""Finite actual production server + browser-style RunController gate."""

import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NODE = "node"


def main():
    (ROOT / "frontend/benchmarks/results").mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        str(ROOT / p) for p in ("contracts/python", "backend", "runner")
    )
    environment["CODETRACE_FRONTEND_DIST"] = str(ROOT / "frontend/dist")
    environment["CODETRACE_IMAGE_IDENTITY"] = str(ROOT / "backend/local-image.json")
    environment["CODETRACE_MODE"] = "production"
    started_ns = time.time_ns()
    started = str(started_ns // 1_000_000_000)
    with tempfile.TemporaryFile() as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "codetrace_backend.app"],
            cwd=ROOT,
            env=environment,
            stdout=log,
            stderr=log,
        )
        try:
            deadline = time.monotonic() + 25
            while time.monotonic() < deadline:
                assert process.poll() is None, "production entrypoint failed"
                try:
                    with urllib.request.urlopen(
                        "http://127.0.0.1:8000/api/health", timeout=5
                    ) as response:
                        assert json.load(response)["available"]
                        break
                except urllib.error.URLError:
                    time.sleep(0.05)
            else:
                raise AssertionError("production startup deadline")
            # A second process must fail at bind before any cleanup/lifespan runs.
            second = subprocess.run(
                [sys.executable, "-m", "codetrace_backend.app"],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                timeout=10,
            )
            assert second.returncode != 0 and b"Address already in use" in second.stderr
            result = subprocess.run(
                [NODE, "frontend/tests/examples/production_smoke.ts"],
                cwd=ROOT,
                env=environment,
                timeout=150,
            )
            assert result.returncode == 0
        finally:
            if process.poll() is None:
                process.send_signal(signal.SIGINT)
                try:
                    process.wait(timeout=25)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                    raise
        assert process.returncode == 0
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 8000))
    events = (
        subprocess.check_output(
            [
                "docker",
                "events",
                "--since",
                started,
                "--until",
                str(int(time.time()) + 1),
                "--filter",
                "event=start",
                "--filter",
                "label=codetrace.managed=true",
                "--format",
                "{{json .}}",
            ],
            timeout=10,
        )
        .decode()
        .splitlines()
    )
    decoded = [json.loads(e) for e in events]
    # Docker --since with integer seconds includes the preceding smoke in the
    # same second. Run ownership, not the shared daemon window, defines this test.
    starts = [e["Actor"]["Attributes"]["codetrace.run_id"] for e in decoded]
    report_path = ROOT / "frontend/benchmarks/results/examples-production.json"
    report = json.loads(report_path.read_text())
    owned_ids = report["acceptedRunIds"]
    assert len(owned_ids) == len(set(owned_ids)) == report["counts"]["accepted"]
    for run_id in owned_ids:
        assert starts.count(run_id) == 1, (run_id, starts.count(run_id))
    owned_starts = [run_id for run_id in starts if run_id in set(owned_ids)]
    for row in report["results"]:
        row["pythonExecutions"] = starts.count(row["runId"])
        assert row["pythonExecutions"] == 1, row
    assert len(owned_starts) == report["counts"]["accepted"]
    assert not subprocess.check_output(
        ["docker", "ps", "-aq", "--filter", "label=codetrace.managed=true"], timeout=10
    ).strip()
    assert not list(Path(tempfile.gettempdir()).glob("codetrace-result-v1-*"))
    report["cleanup"] = {
        "managedContainers": 0,
        "resultDirectories": 0,
        "serverStopped": True,
        "portFree": True,
    }
    report["dockerStarts"] = len(owned_starts)
    report["dockerEventEvidence"] = {
        "startedAtNs": started_ns,
        "ownedRunIds": owned_ids,
        "otherRunStartsInDaemonWindow": len(starts) - len(owned_starts),
        "eachAcceptedRunStartedExactlyOnce": True,
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print("Production entrypoint, fixed-port rejection and graceful shutdown passed.", flush=True)


if __name__ == "__main__":
    main()
