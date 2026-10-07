"""Assemble a local bundle from existing production assets, wheel and image identity."""

import shutil
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    if sys.version_info[:3] != (3, 13, 16):
        raise SystemExit("Use CPython 3.13.16 for the verified installation.")
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python scripts/install_local.py DESTINATION")
    target = Path(sys.argv[1]).resolve()
    # Never overwrite an existing installation or user directory.
    if target.exists():
        raise SystemExit("Destination already exists; choose a new local installation directory.")
    wheels = list((ROOT / "dist").glob("codetrace_contracts-1.0.0-*.whl"))
    if len(wheels) != 1 or not (ROOT / "frontend/dist/index.html").is_file():
        raise SystemExit("Build the production frontend and exactly one v1.0.0 wheel first.")
    identity = ROOT / "backend/local-image.json"
    if not identity.is_file():
        raise SystemExit("Build and verify the Runner image first.")
    target.mkdir(parents=True)
    shutil.copytree(ROOT / "frontend/dist", target / "frontend")
    shutil.copyfile(identity, target / "local-image.json")
    shutil.copyfile(ROOT / "requirements.lock", target / "requirements.lock")
    shutil.copyfile(ROOT / "scripts/run_local.py", target / "run.py")
    wheel = target / wheels[0].name
    shutil.copyfile(wheels[0], wheel)
    venv.EnvBuilder(with_pip=True).create(target / "venv")
    python = target / "venv/bin/python"
    subprocess.run(
        [str(python), "-I", "-m", "pip", "install", "-r", str(target / "requirements.lock")],
        check=True,
    )
    subprocess.run(
        [str(python), "-I", "-m", "pip", "install", "--no-deps", "--no-index", str(wheel)],
        check=True,
    )
    print("Installed local bundle. Start with:")
    print(f'"{python}" -I "{target / "run.py"}"')


if __name__ == "__main__":
    main()
