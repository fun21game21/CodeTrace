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
                [NODE, "frontend/tests/viewer/production_smoke.ts"],
                cwd=ROOT,
                env=environment,
                timeout=100,
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
    print("Production entrypoint, fixed-port rejection and graceful shutdown passed.", flush=True)


if __name__ == "__main__":
    main()
