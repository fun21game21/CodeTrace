"""Atomic bounded NDJSON sink and streaming validated TraceDocument assembly."""

import asyncio
import hashlib
import json
import os
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, cast

from codetrace_contracts import default_limits, validate_contract
from codetrace_contracts.config import RECORDER_DISK_BYTES
from codetrace_contracts.types import (
    ConsoleEntry,
    ResourceLimits,
    RunEndEvent,
    TransportTraceRecord,
)

from ..runs import Clock, SystemClock, TerminalResult
from ..runs.protocol import Prefix, constant, encode, pairs
from .completion import build_completion
from .io import FileOps, owned_io
from .storage import create_directory
from .types import RecorderError, RunMetadata


def check(condition: bool) -> None:
    if not condition:
        raise RecorderError()


@dataclass(frozen=True, slots=True)
class SealedFile:
    path: Path
    traceBytes: int
    eventCount: int


class TraceRecorder:
    def __init__(
        self,
        run_id: str,
        limits: ResourceLimits | None = None,
        *,
        temp_root: Path | None = None,
        disk_budget: int = RECORDER_DISK_BYTES,
        file_ops: FileOps | None = None,
    ) -> None:
        self.runId = run_id
        self._limits = default_limits() if limits is None else limits.copy()
        validate_contract(self._limits, "trace", "ResourceLimits")
        check(0 < disk_budget <= RECORDER_DISK_BYTES)
        check(self._limits["terminal_reserve_bytes"] < self._limits["trace_bytes"])
        self.directory = create_directory(run_id, temp_root)
        self._ops = FileOps() if file_ops is None else file_ops
        self._disk_budget = disk_budget
        self._marker_bytes = (self.directory / "owner.json").stat().st_size
        self._files: dict[str, BinaryIO] = {}
        try:
            check(self._marker_bytes <= disk_budget)
            for name in ("states", "events", "console"):
                path = self.directory / (name + ".ndjson")
                self._files[name] = path.open("xb+", buffering=0)
                os.chmod(path, 0o600)
        except BaseException:
            for file in self._files.values():
                file.close()
            self._ops.remove_directory(self.directory)
            raise
        self._offsets = dict.fromkeys(self._files, 0)
        self._hashes = {name: hashlib.sha256() for name in self._files}
        self._prefix = Prefix()
        self._lock = asyncio.Lock()
        self._closed = self._sealed = self._poisoned = False

    @property
    def limits(self) -> ResourceLimits:
        return self._limits.copy()

    @property
    def counters(self) -> dict[str, int]:
        return {
            "stateCount": self._prefix.version + 1,
            "eventCount": self._prefix.events,
            "consoleCount": self._prefix.console,
            "lastStateVersion": self._prefix.version,
            "lastEventIndex": self._prefix.events - 1,
            "ordinaryBytes": self._prefix.ordinaryBytes,
            "pendingStateBytes": self._prefix.pendingStateBytes,
            "spoolBytes": self._marker_bytes + sum(self._offsets.values()),
        }

    def _rollback(self) -> None:
        for name, file in self._files.items():
            file.truncate(self._offsets[name])
            file.seek(self._offsets[name])

    async def _commit(
        self,
        records: list[TransportTraceRecord] | None,
        entry: ConsoleEntry | None,
    ) -> None:
        async with self._lock:
            check(not self._closed and not self._sealed and not self._poisoned)

            def transaction() -> tuple[Prefix, dict[str, int], dict[str, Any]]:
                self._rollback()  # Uncommitted fault tails are never reused.
                if records is not None:
                    check(len(records) <= self._limits["runner_message_bytes"])
                    total = 0
                    for item in records:
                        validate_contract(item, "runner", "TransportTraceRecord")
                        total += len(encode(item)) + 1
                        check(total <= self._limits["runner_message_bytes"])
                    candidate = self._prefix.chunk(records, self._limits)
                else:
                    check(entry is not None)
                    validate_contract(entry, "trace", "ConsoleEntry")
                    candidate = self._prefix.output(cast(ConsoleEntry, entry), self._limits)
                offsets = self._offsets.copy()
                hashes = {name: value.copy() for name, value in self._hashes.items()}

                def append(name: str, value: object) -> None:
                    data = encode(value) + b"\n"
                    check(
                        self._marker_bytes + sum(offsets.values()) + len(data) <= self._disk_budget
                    )
                    self._ops.write(self._files[name], data, "commit." + name)
                    offsets[name] += len(data)
                    hashes[name].update(data)

                if records is not None:
                    for item in records:
                        append("states" if item["kind"] == "state" else "events", item["record"])
                else:
                    append("console", entry)
                return candidate, offsets, hashes

            try:
                candidate, offsets, hashes = await owned_io(transaction)
            except BaseException:
                # Keep exclusive ownership until even a cancelled worker has stopped.
                try:
                    await owned_io(self._rollback, propagate_cancellation=False)
                except BaseException:
                    self._poisoned = True
                    raise RecorderError() from None
                raise
            # No await between successful I/O and publishing the commit boundary.
            self._prefix, self._offsets, self._hashes = candidate, offsets, hashes

    async def commit_chunk(self, records: list[TransportTraceRecord]) -> None:
        await self._commit(records, None)

    async def commit_console(self, entry: ConsoleEntry) -> None:
        await self._commit(None, entry)

    def _lines(self, name: str) -> Iterator[tuple[bytes, Any]]:
        file = self._files[name]
        file.seek(0)
        remaining = self._offsets[name]
        digest = hashlib.sha256()
        while remaining:
            raw = file.readline(min(remaining, self._limits["runner_message_bytes"] + 2))
            check(bool(raw) and raw.endswith(b"\n") and len(raw) <= remaining)
            remaining -= len(raw)
            digest.update(raw)
            try:
                value = json.loads(
                    raw.decode("ascii"), object_pairs_hook=pairs, parse_constant=constant
                )
            except (ValueError, UnicodeError, RecursionError):
                raise RecorderError() from None
            check(encode(value) + b"\n" == raw)
            yield raw[:-1], value
        check(digest.digest() == self._hashes[name].digest())

    async def seal(
        self,
        metadata: RunMetadata,
        terminal: TerminalResult,
        *,
        deadline: float,
        clock: Clock | None = None,
    ) -> SealedFile:
        clock = SystemClock() if clock is None else clock
        async with self._lock:
            check(not self._closed and not self._sealed and not self._poisoned)
            check(terminal.traceUsable and terminal.cleanupConfirmed)
            check(metadata.runId == terminal.runId == self.runId)
            check(
                metadata.acceptedAt == terminal.acceptedAt and dict(metadata.limits) == self._limits
            )
            p = self._prefix
            check(
                p.version >= 0
                and p.events == terminal.eventCount
                and p.console == terminal.consoleCount
            )
            if terminal.finalStateVersion is not None:
                check(0 <= terminal.finalStateVersion <= p.version)
            completion = build_completion(terminal, p.events)
            if completion["traceComplete"]:
                check(terminal.finalStateVersion == p.version)
                check(p.pendingStateBytes <= self._limits["terminal_reserve_bytes"])
            else:
                check(
                    p.ordinaryBytes + p.pendingStateBytes
                    <= self._limits["trace_bytes"] - self._limits["terminal_reserve_bytes"]
                )
            version = 0 if completion["stateAccuracy"] == "unavailable" else p.version
            check(version == p.version and p.events < self._limits["max_events"])
            end: RunEndEvent = {
                "index": p.events,
                "kind": "RUN_END",
                "stateVersion": version,
                "frameId": None,
                "source": None,
                "consoleOffset": p.console,
                "payload": {},
            }
            validate_contract(end, "trace", "RUN_ENDEvent")
            stop = threading.Event()

            def assemble() -> SealedFile:
                temporary = self.directory / "trace.tmp"
                destination = self.directory / "trace.json"
                size = 0
                digest = hashlib.sha256()

                def guard() -> None:
                    if stop.is_set() or clock.now() >= deadline:
                        raise RecorderError("FINALIZATION_TIMEOUT")

                def write(file: BinaryIO, data: bytes, stage: str) -> None:
                    nonlocal size
                    guard()
                    if size + len(data) > self._limits["trace_bytes"]:
                        # No oversized artifact or silent truncation of committed prefix.
                        raise RecorderError("TRACE_RECORDER_FAILURE")
                    check(self.counters["spoolBytes"] + size + len(data) <= self._disk_budget)
                    self._ops.write(file, data, stage)
                    digest.update(data)
                    size += len(data)

                try:
                    self._rollback()
                    with temporary.open("xb", buffering=0) as file:
                        os.chmod(temporary, 0o600)
                        write(file, encode(metadata.header())[:-1], "seal.header")
                        # Schema/fixture field order, independent of spool layout.
                        for name in ("events", "states", "console"):
                            write(file, b',"' + name.encode() + b'":[', "seal.array")
                            count = 0
                            previous_version = previous_offset = -1
                            for raw, value in self._lines(name):
                                guard()
                                definition = {
                                    "events": "PythonTraceEvent",
                                    "states": "StateRecord",
                                    "console": "ConsoleEntry",
                                }[name]
                                validate_contract(value, "trace", definition)
                                if name == "states":
                                    check(value["version"] == count)
                                    check(
                                        value["kind"]
                                        == (
                                            "checkpoint"
                                            if count % metadata.limits["checkpoint_interval"] == 0
                                            else "delta"
                                        )
                                    )
                                elif name == "events":
                                    check(value["index"] == count)
                                    check(previous_version <= value["stateVersion"] <= p.version)
                                    check(previous_offset <= value["consoleOffset"] <= p.console)
                                    previous_version, previous_offset = (
                                        value["stateVersion"],
                                        value["consoleOffset"],
                                    )
                                else:
                                    check(value["index"] == count)
                                write(file, (b"," if count else b"") + raw, "seal.record")
                                count += 1
                            expected = {
                                "events": p.events,
                                "states": p.version + 1,
                                "console": p.console,
                            }[name]
                            check(count == expected)
                            if name == "events":
                                write(file, (b"," if count else b"") + encode(end), "seal.run_end")
                            write(file, b"]", "seal.array")
                        write(
                            file, b',"completion":' + encode(completion) + b"}", "seal.completion"
                        )
                        self._ops.fsync(file)
                    # Read back the completed file, bounded buffer, before publication.
                    actual = hashlib.sha256()
                    total = 0
                    with temporary.open("rb") as verification_file:
                        while chunk := verification_file.read(65536):
                            guard()
                            total += len(chunk)
                            actual.update(chunk)
                    check(total == size and actual.digest() == digest.digest())
                    guard()
                    self._ops.replace(temporary, destination)
                    os.chmod(destination, 0o400)
                    guard()
                    return SealedFile(destination, size, p.events + 1)
                except BaseException:
                    temporary.unlink(missing_ok=True)
                    destination.unlink(missing_ok=True)
                    raise

            # The same total deadline that began at WATCHDOG FINALIZING, not a new budget.
            async def expire() -> None:
                wake = asyncio.Event()
                while clock.now() < deadline:
                    wake.clear()
                    await clock.wait(wake, deadline - clock.now())

            task = asyncio.create_task(owned_io(assemble, stop))
            timer = asyncio.create_task(expire())
            try:
                done, _ = await asyncio.wait({task, timer}, return_when=asyncio.FIRST_COMPLETED)
                if timer in done and clock.now() >= deadline:
                    task.cancel()
                    try:
                        await task
                    except (asyncio.CancelledError, RecorderError):
                        pass
                    raise RecorderError("FINALIZATION_TIMEOUT")
                result = await task
                self._sealed = True
                return result
            except BaseException:
                task.cancel()
                # owned_io retains the worker until its cooperative stop/rollback.
                while not task.done():
                    try:
                        await asyncio.shield(task)
                    except BaseException:
                        pass
                if not task.cancelled():
                    task.exception()
                # Cancellation can arrive after rename but before publishing.
                await owned_io(
                    lambda: (self.directory / "trace.json").unlink(missing_ok=True),
                    propagate_cancellation=False,
                )
                raise
            finally:
                timer.cancel()
                await asyncio.gather(timer, return_exceptions=True)

    async def dispose(self) -> None:
        async with self._lock:
            if self._closed:
                return

            def remove() -> None:
                for file in self._files.values():
                    file.close()
                if self.directory.exists():
                    self._ops.remove_directory(self.directory)

            await owned_io(remove)
            self._closed = True
