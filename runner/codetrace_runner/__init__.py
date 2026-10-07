"""VALUES–RUNNER_IO Python tracing; production entrypoint: codetrace_runner.runner."""

from .canonical import canonical_json, canonical_size
from .classification import ValueKind, classify_value, is_scalar
from .encoder import TraceEncoder, TraceEncoderSession
from .encoder_errors import EncoderLimitError, TraceEncodingError
from .encoder_types import EncodedEventCore, EncodedReferenceTrace, EncoderAccounting
from .errors import RegistryClosedError, ValueLimitError
from .graph_errors import GraphLimitError
from .graph_types import FrameInput, GraphSnapshot
from .reference import trace_source
from .registry import ObjectRegistry
from .serializer import GraphSerializer
from .trace_engine import ReferenceLimitError, TraceEngine, TraceEngineError
from .trace_types import ReferenceExecution, ReferenceTrace, ReferenceTracePoint
from .values import ScalarEncoder, ValueEncoder

__all__ = [
    "EncodedEventCore",
    "EncodedReferenceTrace",
    "EncoderAccounting",
    "EncoderLimitError",
    "FrameInput",
    "GraphLimitError",
    "GraphSerializer",
    "GraphSnapshot",
    "ObjectRegistry",
    "ReferenceExecution",
    "ReferenceLimitError",
    "ReferenceTrace",
    "ReferenceTracePoint",
    "RegistryClosedError",
    "ScalarEncoder",
    "TraceEngine",
    "TraceEngineError",
    "TraceEncoder",
    "TraceEncoderSession",
    "TraceEncodingError",
    "ValueEncoder",
    "ValueKind",
    "ValueLimitError",
    "canonical_json",
    "canonical_size",
    "classify_value",
    "is_scalar",
    "trace_source",
]
