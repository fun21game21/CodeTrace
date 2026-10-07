"""DOCKER Docker primitives only: no protocol, run state machine or watchdog."""

import asyncio
import json
from uuid import uuid4

from codetrace_contracts import default_limits, validate_contract
from codetrace_contracts.types import ResourceLimits

from .cli import AttachedProcess, CommandExecutor, DockerCLI
from .errors import (
    CleanupError,
    ContainerConflictError,
    CreateOutcomeError,
    DockerCommandTimeout,
    DockerOperationError,
    DockerOutputError,
    DockerUnavailableError,
    ForeignContainerError,
    ImageUnavailableError,
)
from .types import (
    CleanupResult,
    ContainerHandle,
    ContainerInspection,
    ImageIdentity,
    container_id,
    run_identity,
)


def parse_inspection(raw: bytes) -> ContainerInspection:
    try:
        value = json.loads(raw)
        if type(value) is not list or len(value) != 1 or type(value[0]) is not dict:
            raise ValueError
        item = value[0]
        state, config = item["State"], item["Config"]
        labels = {} if config["Labels"] is None else config["Labels"]
        if type(labels) is not dict or any(
            type(k) is not str or type(v) is not str for k, v in labels.items()
        ):
            raise ValueError
        for key in ("Running", "OOMKilled"):
            if type(state[key]) is not bool:
                raise ValueError
        if type(state["ExitCode"]) is not int or type(state["Status"]) is not str:
            raise ValueError
        for field in ("Name", "Image", "Id"):
            if type(item[field]) is not str:
                raise ValueError
        container_id(item["Id"])
        dates: list[str | None] = []
        for key in ("StartedAt", "FinishedAt"):
            date = state.get(key)
            if date is not None and type(date) is not str:
                raise ValueError
            dates.append(None if not date or date.startswith("0001-") else date)
        return ContainerInspection(
            item["Id"],
            item["Name"].removeprefix("/"),
            item["Image"],
            state["Status"],
            state["Running"],
            state["ExitCode"],
            state["OOMKilled"],
            dates[0],
            dates[1],
            labels,
        )
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        raise DockerOutputError("inspect JSON") from None


def managed_handle(inspection: ContainerInspection) -> ContainerHandle | None:
    labels = inspection.labels
    if labels.get("codetrace.managed") != "true":
        return None
    try:
        run_id = run_identity(labels.get("codetrace.run_id", ""))
        return ContainerHandle(inspection.id, run_id, inspection.name)
    except ValueError:
        return None


class DockerAdapter:
    def __init__(
        self,
        image: ImageIdentity,
        limits: ResourceLimits | None = None,
        *,
        executor: CommandExecutor | None = None,
        executable: str = "docker",
    ) -> None:
        effective = default_limits() if limits is None else limits.copy()
        validate_contract(effective, "trace", "ResourceLimits")
        self.image, self.limits = image, effective
        self._executor = DockerCLI() if executor is None else executor
        self._executable = executable

    async def check_available(self) -> str:
        result = await self._executor.run(
            (self._executable, "info", "--format", "{{json .ServerVersion}}"),
            self.limits["cleanup_command_timeout_ms"],
        )
        if result.returncode:
            raise DockerUnavailableError("daemon")
        try:
            version: object = json.loads(result.stdout)
            if type(version) is not str or not version:
                raise ValueError
        except (ValueError, UnicodeError):
            raise DockerUnavailableError("daemon") from None
        return version

    async def _command(self, args: tuple[str, ...], timeout_ms: int | None = None) -> bytes:
        result = await self._executor.run(
            (self._executable, *args),
            self.limits["cleanup_command_timeout_ms"] if timeout_ms is None else timeout_ms,
        )
        if result.returncode:
            await self.check_available()
            raise DockerOperationError(args[0])
        return result.stdout

    async def verify_image(self) -> ImageIdentity:
        result = await self._executor.run(
            (self._executable, "image", "inspect", "--format", "{{json .Id}}", self.image.imageRef),
            self.limits["cleanup_command_timeout_ms"],
        )
        if result.returncode:
            await self.check_available()
            raise ImageUnavailableError("local image")
        try:
            actual: object = json.loads(result.stdout)
        except (ValueError, UnicodeError):
            raise ImageUnavailableError("image identity") from None
        if actual != self.image.imageId:
            raise ImageUnavailableError("image mismatch")
        return self.image

    async def _lookup(self, target: str, *, by_name: bool = False) -> str | None:
        criterion = f"name=^/{target}$" if by_name else f"id={target}"
        raw = await self._command(
            ("container", "ls", "--all", "--no-trunc", "--filter", criterion, "--format", "{{.ID}}")
        )
        try:
            ids = raw.decode("ascii").split()
            if not ids:
                return None  # Successful daemon query, not a failed inspect heuristic.
            if len(ids) != 1:
                raise ValueError
            found = container_id(ids[0])
            if not by_name and found != target:
                raise ValueError
            return found
        except (ValueError, UnicodeError):
            raise DockerOutputError("container lookup") from None

    async def _inspect_id(self, identifier: str) -> ContainerInspection | None:
        if await self._lookup(identifier) is None:
            return None
        try:
            inspection = parse_inspection(await self._command(("container", "inspect", identifier)))
        except DockerOperationError:
            if await self._lookup(identifier) is None:
                return None
            raise
        if inspection.id != identifier:
            raise DockerOutputError("inspect identity")
        return inspection

    async def inspect(self, handle: ContainerHandle) -> ContainerInspection | None:
        inspection = await self._inspect_id(handle.id)
        if inspection is not None and managed_handle(inspection) != handle:
            raise ForeignContainerError("ownership verification")
        return inspection

    def create_argv(self, run_id: str, token: str) -> tuple[str, ...]:
        run_identity(run_id)
        run_identity(token)
        limits = self.limits
        return (
            self._executable,
            "create",
            "--pull=never",
            "--name",
            f"codetrace-{run_id}",
            "--label",
            "codetrace.managed=true",
            "--label",
            f"codetrace.run_id={run_id}",
            "--label",
            f"codetrace.create_token={token}",
            "--env",
            f"CODETRACE_RUN_ID={run_id}",
            "--network=none",
            "--user=65532:65532",
            "--read-only",
            "--tmpfs",
            f"/tmp:rw,noexec,nosuid,size={limits['tmpfs_bytes']},mode=1777",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges=true",
            "--init",
            "--restart=no",
            "--log-driver=none",
            "--memory",
            str(limits["memory_bytes"]),
            "--memory-swap",
            str(limits["memory_plus_swap_bytes"]),
            "--cpus",
            str(limits["cpus"]),
            "--pids-limit",
            str(limits["pids_limit"]),
            "--interactive",
            "--attach=stdin",
            "--attach=stdout",
            "--attach=stderr",
            # Pinned ID closes the tag-change race after verify_image; never a pull.
            self.image.imageId,
        )

    async def create(self, run_id: str) -> ContainerHandle:
        run_identity(run_id)
        await self.verify_image()
        name = f"codetrace-{run_id}"
        existing = await self._lookup(name, by_name=True)
        if existing is not None:
            inspection = await self._inspect_id(existing)
            raise ContainerConflictError(
                inspection is not None and managed_handle(inspection) is not None
            )
        token = str(uuid4())
        try:
            result = await self._executor.run(
                self.create_argv(run_id, token), self.limits["startup_timeout_ms"]
            )
            if result.returncode:
                await self.check_available()
                await self.verify_image()
                raise DockerOperationError("create")
            try:
                identifier = container_id(result.stdout.decode("ascii").strip())
            except (ValueError, UnicodeError):
                raise DockerOutputError("create ID") from None
            handle = ContainerHandle(identifier, run_id, name)
            inspection = await self.inspect(handle)
            if (
                inspection is None
                or inspection.imageId != self.image.imageId
                or inspection.labels.get("codetrace.create_token") != token
            ):
                raise DockerOutputError("created container verification")
            return handle
        except asyncio.CancelledError:
            await self._recover_create(name, run_id, token)
            raise
        except DockerOperationError as error:
            confirmed = await self._recover_create(name, run_id, token)
            raise CreateOutcomeError(confirmed, error.reason) from None

    async def _recover_create(self, name: str, run_id: str, token: str) -> bool:
        try:
            identifier = await self._lookup(name, by_name=True)
            if identifier is None:
                return True
            handle = ContainerHandle(identifier, run_id, name)
            inspection = await self.inspect(handle)
            if inspection is None:
                return await self._lookup(name, by_name=True) is None
            if inspection.labels.get("codetrace.create_token") != token:
                return False  # A competing create is never mistaken for this attempt.
            return (await self.cleanup(handle)).cleanupConfirmed
        except DockerOperationError:
            return False

    async def start_attach(self, handle: ContainerHandle) -> AttachedProcess:
        inspection = await self.inspect(handle)
        if inspection is None or inspection.status != "created":
            raise DockerOperationError("start requires new container")
        attachment = await self._executor.attach(
            (self._executable, "start", "--attach", "--interactive", handle.id),
            self.limits["startup_timeout_ms"],
        )
        # Bound START operation only. The attachment itself has no execution timer.
        try:
            async with asyncio.timeout(self.limits["startup_timeout_ms"] / 1000):
                while True:
                    inspection = await self.inspect(handle)
                    if inspection is not None and (
                        inspection.running or inspection.status in ("exited", "dead")
                    ):
                        return attachment
                    if attachment.returncode is not None:
                        await self.check_available()
                        raise DockerOperationError("start/attach")
                    await asyncio.sleep(0.02)
        except BaseException as error:
            await attachment.close()
            if isinstance(error, TimeoutError):
                raise DockerCommandTimeout("start/attach") from None
            raise

    async def wait(self, handle: ContainerHandle, *, timeout_ms: int | None = None) -> int:
        if await self.inspect(handle) is None:
            raise DockerOperationError("wait missing container")
        raw = await self._command(("wait", handle.id), timeout_ms)
        try:
            text = raw.decode("ascii").strip()
            if not text.isdigit() or not 0 <= int(text) <= 255:
                raise ValueError
            return int(text)
        except (ValueError, UnicodeError):
            raise DockerOutputError("wait status") from None

    async def kill(self, handle: ContainerHandle) -> bool:
        inspection = await self.inspect(handle)
        if inspection is None or not inspection.running:
            return False
        try:
            await self._command(("kill", "--signal=KILL", handle.id))
        except DockerOperationError:
            inspection = await self.inspect(handle)
            if inspection is None or not inspection.running:
                return False
            raise
        return True

    async def _absent(self, handle: ContainerHandle) -> bool:
        return (
            await self._lookup(handle.id) is None
            and await self._lookup(handle.name, by_name=True) is None
        )

    async def remove(self, handle: ContainerHandle) -> None:
        inspection = await self.inspect(handle)
        if inspection is not None:
            try:
                await self._command(("rm", handle.id))
            except DockerOperationError:
                if not await self._absent(handle):
                    raise
        if not await self._absent(handle):
            raise CleanupError("removal confirmation")

    async def cleanup(self, handle: ContainerHandle) -> CleanupResult:
        for attempt in range(1, self.limits["cleanup_attempts"] + 1):
            try:
                inspection = await self.inspect(handle)
                if inspection is not None:
                    if inspection.running:
                        await self.kill(handle)
                        await self.wait(handle)
                    # Explicit post-kill/exit inspection, separate from CLI lifetime.
                    await self.inspect(handle)
                await self.remove(handle)
                return CleanupResult(True, attempt)
            except DockerOperationError:
                pass
        return CleanupResult(
            False, self.limits["cleanup_attempts"], CleanupError("cleanup attempts")
        )

    async def list_managed_containers(self) -> tuple[ContainerHandle, ...]:
        raw = await self._command(
            (
                "container",
                "ls",
                "--all",
                "--no-trunc",
                "--filter",
                "label=codetrace.managed=true",
                "--format",
                "{{.ID}}",
            )
        )
        handles: list[ContainerHandle] = []
        try:
            ids = [container_id(part) for part in raw.decode("ascii").split()]
        except (ValueError, UnicodeError):
            raise DockerOutputError("managed enumeration") from None
        for identifier in ids:
            inspection = await self._inspect_id(identifier)
            if inspection is not None:
                handle = managed_handle(inspection)
                if handle is not None:
                    handles.append(handle)
        return tuple(handles)

    async def inspect_managed_container(
        self, handle: ContainerHandle
    ) -> ContainerInspection | None:
        return await self.inspect(handle)

    async def cleanup_managed_container(self, handle: ContainerHandle) -> CleanupResult:
        return await self.cleanup(handle)
