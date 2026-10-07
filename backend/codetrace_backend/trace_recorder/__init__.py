"""RECORDER temporary recorder and framework-independent result lifecycle."""

from .completion import build_completion
from .io import FileOps
from .recorder import SealedFile, TraceRecorder
from .service import PublicationHandle, ResultService, TraceAccess
from .storage import cleanup_stale_results
from .types import RecorderError, ResultAccessError, ResultSummary, RetainedTraceResult, RunMetadata

__all__ = [
    "FileOps",
    "PublicationHandle",
    "RecorderError",
    "ResultAccessError",
    "ResultService",
    "ResultSummary",
    "RetainedTraceResult",
    "RunMetadata",
    "SealedFile",
    "TraceAccess",
    "TraceRecorder",
    "build_completion",
    "cleanup_stale_results",
]
