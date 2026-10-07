"""One monotonic accounting owner and a wakeable deadline clock."""

import asyncio
import time
from datetime import UTC, datetime
from typing import Protocol

from codetrace_contracts.types import ReasonCode, ResourceLimits, RunStatus

from .types import InvariantError

TERMINAL = frozenset({"SUCCEEDED", "FAILED", "LIMIT_EXCEEDED", "INTERRUPTED"})
TRANSITIONS: dict[str, frozenset[str]] = {
    "STARTING": frozenset({"RUNNING", "FINALIZING"}),
    "RUNNING": frozenset({"WAITING_INPUT", "FINALIZING"}),
    "WAITING_INPUT": frozenset({"RUNNING", "FINALIZING"}),
    "FINALIZING": TERMINAL,
}


class Clock(Protocol):
    def now(self) -> float: ...
    def utc(self) -> str: ...
    async def wait(self, wake: asyncio.Event, delay: float) -> None: ...


class SystemClock:
    def now(self) -> float:
        return time.monotonic()

    def utc(self) -> str:
        return datetime.now(UTC).isoformat().replace("+00:00", "Z")

    async def wait(self, wake: asyncio.Event, delay: float) -> None:
        try:
            async with asyncio.timeout(max(0, delay)):
                await wake.wait()
        except TimeoutError:
            pass


class Timing:
    def __init__(self, clock: Clock, limits: ResourceLimits) -> None:
        self.clock, self.limits = clock, limits
        self.status: RunStatus = "STARTING"
        self.since = self.accepted = clock.now()
        self.started: float | None = None
        self.active = self.waiting = 0.0

    def transition(self, target: RunStatus) -> None:
        if target not in TRANSITIONS.get(self.status, frozenset()):
            raise InvariantError("Invalid RunStatus transition")
        now = self.clock.now()
        if self.status == "RUNNING":
            self.active += now - self.since
        elif self.status == "WAITING_INPUT":
            self.waiting += now - self.since
        self.status, self.since = target, now

    def elapsed(self) -> tuple[float, float]:
        interval = self.clock.now() - self.since
        return (
            1000 * (self.active + (interval if self.status == "RUNNING" else 0)),
            1000 * (self.waiting + (interval if self.status == "WAITING_INPUT" else 0)),
        )

    def deadline(self) -> tuple[float, ReasonCode] | None:
        deadlines: list[tuple[float, ReasonCode]] = []
        limits = self.limits
        if self.status == "STARTING":
            deadlines.append(
                (self.accepted + limits["startup_timeout_ms"] / 1000, "STARTUP_TIMEOUT")
            )
        if self.status == "RUNNING":
            deadlines.append(
                (self.since + limits["active_timeout_ms"] / 1000 - self.active, "ACTIVE_TIMEOUT")
            )
        if self.status == "WAITING_INPUT":
            deadlines.append(
                (self.since + limits["input_idle_timeout_ms"] / 1000, "INPUT_IDLE_TIMEOUT")
            )
        if self.status in {"STARTING", "RUNNING", "WAITING_INPUT"} and self.started is not None:
            deadlines.append(
                (self.started + limits["container_lifetime_ms"] / 1000, "CONTAINER_LIFETIME")
            )
        return min(deadlines, key=lambda item: item[0]) if deadlines else None
