"""Build and serve the explicit production benchmark, with local JSON evidence.

The default driver owns one isolated Chromium process. For manual/browser-tool
acceptance use --manual-browser and close that tab with its owning driver.
The page posts a local report; server and owned browser then close automatically.
The same data and seed are reused for every round. No production telemetry.
"""

import argparse
import json
import os
import platform
import shutil
import subprocess
import tempfile
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--round", required=True, help="Fresh evidence name; existing reports are protected"
    )
    parser.add_argument("--mode", choices=("direct", "worker"), default="direct")
    parser.add_argument(
        "--manual-browser",
        action="store_true",
        help="Open printed URL with your browser driver (manual local browser)",
    )
    parser.add_argument(
        "--browser", help="Chromium/Chrome executable for the isolated headless run"
    )
    parser.add_argument("--skip-build", action="store_true")
    args = parser.parse_args()
    environment = os.environ.copy()
    fixtures = Path(tempfile.gettempdir()) / "codetrace-performance-fixtures"
    output = ROOT / "frontend/benchmarks/results"
    output.mkdir(parents=True, exist_ok=True)
    if (output / (args.round + "-report.json")).exists():
        parser.error("Report already exists; choose a new --round")
    if not args.skip_build:
        subprocess.run(
            ["node", "frontend/tests/performance/fixtures.ts", str(fixtures)],
            cwd=ROOT,
            env=environment,
            check=True,
        )
        subprocess.run(
            ["pnpm", "exec", "vite", "build", "--config", "frontend/benchmarks/vite.config.ts"],
            cwd=ROOT,
            env=environment,
            check=True,
        )
    manifest = json.loads((fixtures / "manifest.json").read_text())
    by_id = {item["runId"]: fixtures / (item["name"] + ".json") for item in manifest}
    counters = {key: {"gets": 0, "releases": 0} for key in by_id}
    finished = threading.Event()
    failed = threading.Event()

    def sysctl(name):
        try:
            return subprocess.check_output(["sysctl", "-n", name], text=True).strip()
        except (OSError, subprocess.CalledProcessError):
            return None

    info = {
        "os": platform.platform(),
        "architecture": platform.machine(),
        "cpu": sysctl("machdep.cpu.brand_string"),
        "ramBytes": sysctl("hw.memsize"),
        "node": subprocess.check_output(["node", "--version"], env=environment, text=True).strip(),
        "round": args.round,
    }

    class Handler(SimpleHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/manifest.json":
                return self.respond(json.dumps(manifest).encode())
            if self.path == "/environment.json":
                return self.respond(json.dumps(info).encode())
            if self.path == "/network.json":
                return self.respond(json.dumps(counters).encode())
            parts = self.path.split("/")
            if (
                len(parts) == 5
                and parts[1:3] == ["api", "runs"]
                and parts[4] == "trace"
                and parts[3] in by_id
            ):
                counters[parts[3]]["gets"] += 1
                path = by_id[parts[3]]
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(path.stat().st_size))
                self.end_headers()
                with path.open("rb") as stream:
                    self.copyfile(stream, self.wfile)
                return
            return super().do_GET()

        def respond(self, raw):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            if length > 8 * 1024**2:
                return self.send_error(413)
            raw = self.rfile.read(length)
            parts = self.path.split("/")
            if len(parts) == 5 and parts[3] in counters and parts[4] == "release":
                counters[parts[3]]["releases"] += 1
            if self.path in ("/report", "/progress", "/failure"):
                parsed = json.loads(raw)
                suffix = self.path[1:]
                (output / (args.round + "-" + suffix + ".json")).write_text(
                    json.dumps(parsed, indent=2) + "\n"
                )
                print(
                    suffix
                    + ": "
                    + str(parsed.get("name", parsed.get("decision", parsed.get("error", "")))),
                    flush=True,
                )
            self.send_response(204)
            self.end_headers()
            if self.path in ("/report", "/failure"):
                if self.path == "/failure":
                    failed.set()
                finished.set()
                threading.Thread(target=server.shutdown, daemon=True).start()

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(
        ("127.0.0.1", 8765), partial(Handler, directory=str(ROOT / "frontend/benchmarks/dist"))
    )
    url = "http://127.0.0.1:8765/?mode=" + args.mode
    print("PERFORMANCE benchmark: " + url, flush=True)
    browser = None
    profile = tempfile.TemporaryDirectory(prefix="codetrace-performance-browser-")
    deadline = threading.Timer(600, server.shutdown)
    try:
        if not args.manual_browser:
            executable = args.browser or shutil.which("chromium") or shutil.which("google-chrome")
            mac_chrome = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
            if executable is None and mac_chrome.is_file():
                executable = str(mac_chrome)
            if executable is None:
                parser.error("Pass --browser /path/to/Chromium or --manual-browser")
            browser = subprocess.Popen(
                [
                    executable,
                    "--headless=new",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--disable-background-timer-throttling",
                    "--window-size=1440,900",
                    "--user-data-dir=" + profile.name,
                    url,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        deadline.start()
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        deadline.cancel()
        server.server_close()
        if browser is not None:
            browser.terminate()
            try:
                browser.wait(timeout=10)
            except subprocess.TimeoutExpired:
                browser.kill()
                browser.wait(timeout=5)
        profile.cleanup()
        print("PERFORMANCE benchmark server/browser closed", flush=True)
    return int(not finished.is_set() or failed.is_set())


if __name__ == "__main__":
    raise SystemExit(main())
