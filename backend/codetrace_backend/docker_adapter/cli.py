"""Async argv subprocesses; bounded command output and process reaping."""

import asyncio
from dataclasses import dataclass
from typing import Protocol

from .errors import DockerCommandTimeout, DockerOutputError, DockerUnavailableError

COMMAND_OUTPUT_BYTES = 1024 * 1024


@dataclass(frozen=True, slots=True)
class CommandResult:
    returncode: int
    stdout: bytes
    stderr: bytes


async def stop_process(process: asyncio.subprocess.Process) -> None:
    # Stopping this CLI is NEVER interpreted as stopping a container.
    if process.returncode is None:
        try:
            process.kill()
        except ProcessLookupError:
            pass

    # Readers must be drained concurrently, including after CLI termination.
    async def drain(reader: asyncio.StreamReader | None) -> None:
        if reader is not None:
            while await reader.read(4096):
                pass

    if process.stdin is not None:
        process.stdin.close()
    await asyncio.gather(drain(process.stdout), drain(process.stderr), process.wait())


class AttachedProcess:
    """Caller drains BOTH readers concurrently and finishes/cancels them before close.

    wait_cli is used after stream consumers finish; container wait is independent.
    No protocol parsing or lifetime timer is installed here.
    """

    def __init__(self, process: asyncio.subprocess.Process) -> None:
        if process.stdin is None or process.stdout is None or process.stderr is None:
            raise DockerOutputError("attach pipes")
        self._process = process
        self.stdin = process.stdin
        self.stdout = process.stdout
        self.stderr = process.stderr

    @property
    def returncode(self) -> int | None:
        return self._process.returncode

    async def wait_cli(self, timeout_ms: int) -> int:
        # Lifetime is caller-chosen; no automatic active/input/container timer.
        try:
            async with asyncio.timeout(timeout_ms / 1000):
                return await self._process.wait()
        except TimeoutError:
            await self.close()
            raise DockerCommandTimeout("attached CLI wait") from None
        except asyncio.CancelledError:
            await self.close()
            raise

    async def close(self) -> None:
        await stop_process(self._process)


class CommandExecutor(Protocol):
    async def run(self, argv: tuple[str, ...], timeout_ms: int) -> CommandResult: ...
    async def attach(self, argv: tuple[str, ...], timeout_ms: int) -> AttachedProcess: ...


class DockerCLI:
    async def _spawn(self, argv: tuple[str, ...], timeout_ms: int) -> asyncio.subprocess.Process:
        try:
            async with asyncio.timeout(timeout_ms / 1000):
                return await asyncio.create_subprocess_exec(
                    *argv,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    limit=65536,
                )
        except FileNotFoundError:
            raise DockerUnavailableError("executable") from None
        except TimeoutError:
            raise DockerCommandTimeout("spawn") from None
        except OSError:
            raise DockerUnavailableError("executable") from None

    async def run(self, argv: tuple[str, ...], timeout_ms: int) -> CommandResult:
        process = await self._spawn(argv, timeout_ms)
        assert process.stdout is not None and process.stderr is not None
        if process.stdin is not None:
            process.stdin.close()

        async def collect(reader: asyncio.StreamReader) -> bytes:
            parts: list[bytes] = []
            size = 0
            while chunk := await reader.read(4096):
                size += len(chunk)
                if size > COMMAND_OUTPUT_BYTES:
                    raise DockerOutputError("command output")
                parts.append(chunk)
            return b"".join(parts)

        tasks = [
            asyncio.create_task(collect(process.stdout)),
            asyncio.create_task(collect(process.stderr)),
        ]
        try:
            async with asyncio.timeout(timeout_ms / 1000):
                stdout, stderr = await asyncio.gather(*tasks)
                code = await process.wait()
            return CommandResult(code, stdout, stderr)
        except BaseException as error:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await stop_process(process)
            if isinstance(error, TimeoutError):
                raise DockerCommandTimeout("command") from None
            raise

    async def attach(self, argv: tuple[str, ...], timeout_ms: int) -> AttachedProcess:
        return AttachedProcess(await self._spawn(argv, timeout_ms))
