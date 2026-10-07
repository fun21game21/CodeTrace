"""Bounded IPC decoding and accepted-prefix counters, without trace storage."""

import asyncio
import json
from dataclasses import dataclass, replace
from typing import Any, cast

from codetrace_contracts import validate_contract
from codetrace_contracts.types import (
    ConsoleEntry,
    ResourceLimits,
    RunnerMessage,
    TransportTraceRecord,
)


class ProtocolViolation(ValueError):
    pass


def require(condition: bool) -> None:
    if not condition:
        raise ProtocolViolation("Invalid Runner protocol")


def text_bytes(text: str) -> int:
    return len(text.encode("utf-8", "surrogatepass"))


def encode(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode(
        "ascii"
    )


def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in items:
        require(key not in value)
        value[key] = item
    return value


def constant(value: str) -> None:
    raise ProtocolViolation("Non-JSON constant")


class Reader:
    def __init__(self, stream: asyncio.StreamReader, run_id: str, limit: int) -> None:
        self.stream, self.run_id, self.limit = stream, run_id, limit
        self.buffer = bytearray()
        self.seq = 1

    async def next(self) -> RunnerMessage | None:
        while b"\n" not in self.buffer:
            require(len(self.buffer) <= self.limit)
            chunk = await self.stream.read(min(4096, self.limit + 1 - len(self.buffer)))
            if not chunk:
                require(not self.buffer)
                return None
            self.buffer.extend(chunk)
        end = self.buffer.index(10)
        require(end <= self.limit)
        raw = bytes(self.buffer[:end])
        del self.buffer[: end + 1]
        try:
            value = json.loads(
                raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant
            )
            validate_contract(value, "runner")
            require(value["runId"] == self.run_id and value["seq"] == self.seq)
        except (ValueError, UnicodeError, RecursionError):
            raise ProtocolViolation("Invalid Runner message") from None
        self.seq += 1
        return cast(RunnerMessage, value)


@dataclass
class Prefix:
    version: int = -1
    events: int = 0
    console: int = 0
    consoleBytes: int = 0
    eventVersion: int = -1
    consoleOffset: int = 0
    ordinaryBytes: int = 0
    pendingStateBytes: int = 0

    def chunk(self, records: list[TransportTraceRecord], limits: ResourceLimits) -> "Prefix":
        # Work on a detached candidate. Counters publish only after atomic sink success.
        result = replace(self)
        for item in records:
            size = len(encode(item["record"])) + 1
            if item["kind"] == "state":
                record = item["record"]
                require(record["version"] == result.version + 1)
                expected = (
                    "checkpoint"
                    if record["version"] % limits["checkpoint_interval"] == 0
                    else "delta"
                )
                require(record["kind"] == expected)
                require(record["version"] <= result.events + 1)
                if record["kind"] == "checkpoint":
                    state = record["state"]
                    require(len(encode(state)) <= limits["state_bytes"])
                    require(len(state["objects"]) <= limits["state_objects"])
                    require(len(state["frames"]) <= limits["active_frames"])
                result.ordinaryBytes += result.pendingStateBytes
                if record["version"] == 0:
                    result.ordinaryBytes += size
                    result.pendingStateBytes = 0
                else:
                    result.pendingStateBytes = size
                result.version += 1
            else:
                event = item["record"]
                require(event["index"] == result.events < limits["max_events"] - 1)
                require(result.eventVersion <= event["stateVersion"] <= result.version)
                require(result.consoleOffset <= event["consoleOffset"] <= result.console)
                result.eventVersion = event["stateVersion"]
                result.consoleOffset = event["consoleOffset"]
                result.events += 1
                result.ordinaryBytes += result.pendingStateBytes + size
                result.pendingStateBytes = 0
            require(
                result.ordinaryBytes <= limits["trace_bytes"] - limits["terminal_reserve_bytes"]
            )
            require(
                result.pendingStateBytes
                <= max(
                    limits["terminal_reserve_bytes"],
                    limits["trace_bytes"] - limits["terminal_reserve_bytes"] - result.ordinaryBytes,
                )
            )
        return result

    def output(self, entry: ConsoleEntry, limits: ResourceLimits) -> "Prefix":
        result = replace(self)
        size = text_bytes(entry["text"])
        require(entry["index"] == self.console)
        cap = (
            limits["input_value_bytes"] + 1
            if entry["kind"] == "input"
            else limits["output_chunk_bytes"]
        )
        require(size <= cap)
        require(self.consoleBytes + size <= limits["console_bytes"])
        result.console += 1
        result.consoleBytes += size
        result.ordinaryBytes += len(encode(entry)) + 1
        require(result.ordinaryBytes <= limits["trace_bytes"] - limits["terminal_reserve_bytes"])
        return result
