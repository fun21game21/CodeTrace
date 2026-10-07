import asyncio
import sys

import pytest
from codetrace_backend.docker_adapter.cli import AttachedProcess, DockerCLI
from codetrace_backend.docker_adapter.errors import (
    DockerCommandTimeout,
    DockerOutputError,
    DockerUnavailableError,
)


def run(coro):
    return asyncio.run(coro)


def test_no_shell_argv_preserved():
    text = "; touch /tmp/NEVER_EXECUTE $HOME `echo unsafe`"
    result = run(
        DockerCLI().run((sys.executable, "-c", "import sys;print(sys.argv[1])", text), 1000)
    )
    assert result.stdout.decode().strip() == text and result.returncode == 0


def test_absent_executable():
    with pytest.raises(DockerUnavailableError) as caught:
        run(DockerCLI().run(("/nonexistent/codetrace-docker",), 100))
    assert caught.value.kind == "executable"


def test_both_streams_drained_concurrently():
    source = "import os;os.write(1,b'a'*200000);os.write(2,b'b'*200000)"
    result = run(DockerCLI().run((sys.executable, "-c", source), 1000))
    assert result.stdout == b"a" * 200000 and result.stderr == b"b" * 200000


def test_command_output_bounded():
    with pytest.raises(DockerOutputError):
        run(DockerCLI().run((sys.executable, "-c", "import os;os.write(1,b'x'*1100000)"), 1000))


def test_timeout_reaps_actual_cli(monkeypatch):
    original = asyncio.create_subprocess_exec
    processes = []

    async def capture(*args, **kwargs):
        assert "shell" not in kwargs
        process = await original(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", capture)
    with pytest.raises(DockerCommandTimeout):
        run(DockerCLI().run((sys.executable, "-c", "import time;time.sleep(10)"), 50))
    assert processes[0].returncode is not None


def test_cancellation_reaps_actual_cli(monkeypatch):
    original = asyncio.create_subprocess_exec
    processes = []

    async def capture(*args, **kwargs):
        process = await original(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", capture)

    async def scenario():
        task = asyncio.create_task(
            DockerCLI().run((sys.executable, "-c", "import time;time.sleep(10)"), 2000)
        )
        while not processes:
            await asyncio.sleep(0.005)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    run(scenario())
    assert processes[0].returncode is not None


def test_attachment_has_independent_streams_and_explicit_close():
    async def scenario():
        attachment = await DockerCLI().attach(
            (
                sys.executable,
                "-c",
                (
                    "import sys;print('ready',flush=True);"
                    "print('native',file=sys.stderr,flush=True);"
                    "print(sys.stdin.readline(),end='',flush=True)"
                ),
            ),
            1000,
        )
        assert isinstance(attachment, AttachedProcess)
        out, err = await asyncio.gather(attachment.stdout.readline(), attachment.stderr.readline())
        assert out == b"ready\n" and err == b"native\n"
        attachment.stdin.write(b"input\n")
        await attachment.stdin.drain()
        assert await attachment.stdout.readline() == b"input\n"
        assert await attachment.wait_cli(1000) == 0
        await attachment.close()
        await attachment.close()

    run(scenario())


def test_attachment_wait_timeout_reaps_cli_only():
    async def scenario():
        attachment = await DockerCLI().attach(
            (sys.executable, "-c", "import time;time.sleep(10)"), 1000
        )
        with pytest.raises(DockerCommandTimeout):
            await attachment.wait_cli(20)
        assert attachment.returncode is not None

    run(scenario())
