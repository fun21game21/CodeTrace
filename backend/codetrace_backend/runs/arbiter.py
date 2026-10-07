"""First accepted cause, then finalization/cleanup overrides from specification 18."""

from dataclasses import dataclass
from typing import get_args

from codetrace_contracts.types import (
    LimitReasonCode,
    Outcome,
    ReasonCode,
    RunnerSuccessPayload,
    RunnerUserErrorPayload,
    RunStatus,
)

LIMITS = frozenset(get_args(LimitReasonCode))


@dataclass(frozen=True, slots=True)
class Decision:
    status: RunStatus
    outcome: Outcome
    reason: ReasonCode


class TerminalArbiter:
    def __init__(self) -> None:
        self.primaryCause: ReasonCode | None = None
        self.proposal: RunnerSuccessPayload | RunnerUserErrorPayload | None = None
        self.finalizationExpired = False

    def accept(self, reason: ReasonCode) -> bool:
        if self.primaryCause is not None:
            return False
        self.primaryCause = reason
        return True

    def decide(self, *, exit_code: int | None, oom: bool, cleanup: bool) -> Decision:
        reason: ReasonCode
        if not cleanup:
            reason = "CLEANUP_FAILED"
        elif self.finalizationExpired:
            reason = "FINALIZATION_TIMEOUT"
        elif self.primaryCause is not None:
            reason = self.primaryCause
        elif oom:
            reason = "MEMORY_LIMIT"
        elif self.proposal is not None and exit_code == 0:
            if self.proposal["outcome"] == "SUCCESS":
                return Decision("SUCCEEDED", "SUCCESS", "NONE")
            return Decision("FAILED", "USER_ERROR", self.proposal["reason"])
        else:
            reason = "RUNNER_FAILURE"
        if reason in LIMITS:
            return Decision("LIMIT_EXCEEDED", "LIMIT_EXCEEDED", reason)
        if reason in {"CONNECTION_LOST", "BACKEND_SHUTDOWN"}:
            return Decision("INTERRUPTED", "INTERRUPTED", reason)
        return Decision("FAILED", "SYSTEM_ERROR", reason)
