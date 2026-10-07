"""Launch the installed local bundle from any working directory."""

import os
from pathlib import Path


def main():
    root = Path(__file__).resolve().parent
    python = root / "venv/bin/python"
    if not python.is_file():
        raise SystemExit("Local installation is incomplete: venv/bin/python is missing.")
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
    environment.update(
        CODETRACE_MODE="production",
        CODETRACE_FRONTEND_DIST=str(root / "frontend"),
        CODETRACE_IMAGE_IDENTITY=str(root / "local-image.json"),
    )
    os.execve(python, [str(python), "-I", "-m", "codetrace_backend.app"], environment)


if __name__ == "__main__":
    main()
