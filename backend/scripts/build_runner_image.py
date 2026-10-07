"""Explicit preparation only. Build once, verify runtime, record the real ID."""

import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
IDENTITY = ROOT / "backend/local-image.json"


def command(argv, **kwargs):
    return subprocess.run(argv, check=True, cwd=ROOT, **kwargs)


def main():
    command(
        ["docker", "build", "--tag", "codetrace-runner:1.0", "--file", "runner/Dockerfile", "."]
    )
    raw = command(
        ["docker", "image", "inspect", "--format", "{{json .Id}}", "codetrace-runner:1.0"],
        capture_output=True,
        timeout=15,
    ).stdout
    image_id = json.loads(raw)
    from codetrace_backend.docker_adapter import ImageIdentity

    identity = ImageIdentity(image_id)
    # Trusted image-level smoke; no user code, host mounts or network.
    probe = """import json,sys,sysconfig,os
from codetrace_runner.runner import runtime_supported
assert runtime_supported()
assert os.getuid()==65532
print(json.dumps({'pythonVersion':list(sys.version_info),'gilEnabled':sys._is_gil_enabled(),'freeThreaded':bool(sysconfig.get_config_var('Py_GIL_DISABLED')),'jit':bool(sysconfig.get_config_var('_PY_JIT')),'uid':os.getuid()}))
"""
    probe_run = str(uuid4())
    probe_name = "codetrace-" + probe_run
    try:
        result = command(
            [
                "docker",
                "run",
                "--rm",
                "--name",
                probe_name,
                "--label",
                "codetrace.managed=true",
                "--label",
                "codetrace.run_id=" + probe_run,
                "--pull=never",
                "--network=none",
                "--user=65532:65532",
                "--read-only",
                "--tmpfs",
                "/tmp:rw,noexec,nosuid,size=16777216,mode=1777",
                "--cap-drop=ALL",
                "--security-opt=no-new-privileges=true",
                "--init",
                "--restart=no",
                "--log-driver=none",
                "--memory=536870912",
                "--memory-swap=536870912",
                "--cpus=1.0",
                "--pids-limit=16",
                "--entrypoint",
                "python",
                image_id,
                "-c",
                probe,
            ],
            capture_output=True,
            timeout=30,
        )
    finally:
        listed = (
            command(
                [
                    "docker",
                    "container",
                    "ls",
                    "--all",
                    "--no-trunc",
                    "--filter",
                    "name=^/" + probe_name + "$",
                    "--format",
                    "{{.ID}}",
                ],
                capture_output=True,
                timeout=10,
            )
            .stdout.decode()
            .split()
        )
        for cid in listed:
            inspected = json.loads(
                command(
                    ["docker", "container", "inspect", cid], capture_output=True, timeout=10
                ).stdout
            )[0]
            assert inspected["Config"]["Labels"].get("codetrace.run_id") == probe_run
            assert inspected["Config"]["Labels"].get("codetrace.managed") == "true"
            command(["docker", "rm", "--force", cid], capture_output=True, timeout=10)
        assert not command(
            [
                "docker",
                "container",
                "ls",
                "--all",
                "--filter",
                "name=^/" + probe_name + "$",
                "--format",
                "{{.ID}}",
            ],
            capture_output=True,
            timeout=10,
        ).stdout.strip()
    runtime = json.loads(result.stdout)
    temp = IDENTITY.with_suffix(".json.tmp")
    temp.write_text(
        json.dumps({"imageRef": identity.imageRef, "imageId": identity.imageId}, indent=2) + "\n"
    )
    os.replace(temp, IDENTITY)
    print(json.dumps({"imageRef": identity.imageRef, "imageId": image_id, "runtime": runtime}))
    return 0


if __name__ == "__main__":
    sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "contracts/python")]
    raise SystemExit(main())
