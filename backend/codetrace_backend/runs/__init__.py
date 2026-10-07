"""WATCHDOG internal orchestration, with no startup/import side effects."""

from .arbiter import Decision, TerminalArbiter
from .manager import Availability, RunManager, SlotLease
from .session import RunSession
from .timing import Clock, SystemClock, Timing
from .types import (
    InputSubmission,
    InvariantError,
    LifecycleEvent,
    RecordSink,
    RunRejected,
    TerminalResult,
)

__all__ = [
    "Availability",
    "Clock",
    "Decision",
    "InputSubmission",
    "InvariantError",
    "LifecycleEvent",
    "RecordSink",
    "RunManager",
    "RunRejected",
    "RunSession",
    "SlotLease",
    "SystemClock",
    "TerminalArbiter",
    "TerminalResult",
    "Timing",
]
