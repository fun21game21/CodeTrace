"""Manually maintained wire DTOs. Runtime validation is in validators.py."""

from typing import Literal, TypedDict

RunId = str
ObjectId = str
FrameId = str
RequestId = str
Timestamp = str
ReasonCode = Literal[
    "NONE",
    "SYNTAX_ERROR",
    "UNCAUGHT_EXCEPTION",
    "UNSUPPORTED_FEATURE",
    "ACTIVE_TIMEOUT",
    "INPUT_IDLE_TIMEOUT",
    "CONTAINER_LIFETIME",
    "STARTUP_TIMEOUT",
    "MEMORY_LIMIT",
    "EVENT_LIMIT",
    "TRACE_BYTES_LIMIT",
    "REGISTRY_LIMIT",
    "STATE_BYTES_LIMIT",
    "COLLECTION_LIMIT",
    "STRING_LIMIT",
    "INT_DIGITS_LIMIT",
    "CONSOLE_LIMIT",
    "FRAME_LIMIT",
    "INPUT_REQUEST_LIMIT",
    "DOCKER_UNAVAILABLE",
    "IMAGE_UNAVAILABLE",
    "CLIENT_PROTOCOL_ERROR",
    "RUNNER_PROTOCOL_ERROR",
    "RUNNER_FAILURE",
    "TRACE_RECORDER_FAILURE",
    "FINALIZATION_TIMEOUT",
    "CLEANUP_FAILED",
    "CONNECTION_LOST",
    "BACKEND_SHUTDOWN",
]
LimitReasonCode = Literal[
    "ACTIVE_TIMEOUT",
    "INPUT_IDLE_TIMEOUT",
    "CONTAINER_LIFETIME",
    "STARTUP_TIMEOUT",
    "MEMORY_LIMIT",
    "EVENT_LIMIT",
    "TRACE_BYTES_LIMIT",
    "REGISTRY_LIMIT",
    "STATE_BYTES_LIMIT",
    "COLLECTION_LIMIT",
    "STRING_LIMIT",
    "INT_DIGITS_LIMIT",
    "CONSOLE_LIMIT",
    "FRAME_LIMIT",
    "INPUT_REQUEST_LIMIT",
]
ApiValidationCode = Literal[
    "RUN_BUSY",
    "INVALID_START",
    "CODE_TOO_LARGE",
    "INVALID_INPUT",
    "INPUT_ALREADY_ACCEPTED",
    "RESULT_NOT_AVAILABLE",
]
RunStatus = Literal[
    "STARTING",
    "RUNNING",
    "WAITING_INPUT",
    "FINALIZING",
    "SUCCEEDED",
    "FAILED",
    "LIMIT_EXCEEDED",
    "INTERRUPTED",
]
Outcome = Literal["SUCCESS", "USER_ERROR", "LIMIT_EXCEEDED", "SYSTEM_ERROR", "INTERRUPTED"]


class ResourceLimits(TypedDict):
    active_runs: Literal[1]
    retained_results: Literal[1]
    code_bytes: int
    max_events: int
    trace_bytes: int
    terminal_reserve_bytes: int
    registry_objects: int
    state_objects: int
    state_bytes: int
    collection_items: int
    instance_attributes: int
    string_bytes: int
    integer_decimal_digits: int
    active_frames: int
    console_bytes: int
    output_chunk_bytes: int
    input_value_bytes: int
    input_requests: int
    checkpoint_interval: int
    active_timeout_ms: int
    input_idle_timeout_ms: int
    container_lifetime_ms: int
    startup_timeout_ms: int
    result_ttl_ms: int
    cleanup_command_timeout_ms: int
    cleanup_attempts: int
    finalization_timeout_ms: int
    watchdog_tick_ms: int
    memory_bytes: int
    memory_plus_swap_bytes: int
    cpus: float
    pids_limit: int
    tmpfs_bytes: int
    runner_message_bytes: int
    ws_message_bytes: int
    native_stderr_bytes: int
    history_entries: int
    max_visual_nodes: int
    visible_array_items: int


class SourceLocation(TypedDict):
    fileName: Literal["<codetrace-user>"]
    lineNumber: int | None


class NoneValue(TypedDict):
    kind: Literal["scalar"]
    type: Literal["none"]
    value: None


class BoolValue(TypedDict):
    kind: Literal["scalar"]
    type: Literal["bool"]
    value: bool


class IntValue(TypedDict):
    kind: Literal["scalar"]
    type: Literal["int"]
    value: str


class FloatValue(TypedDict):
    kind: Literal["scalar"]
    type: Literal["float"]
    value: str


class StrValue(TypedDict):
    kind: Literal["scalar"]
    type: Literal["str"]
    value: str


Ref = TypedDict("Ref", {"$ref": ObjectId})
Value = NoneValue | BoolValue | IntValue | FloatValue | StrValue | Ref


class Binding(TypedDict):
    name: str
    value: Value


class DictEntry(TypedDict):
    key: Value
    value: Value


class ListObject(TypedDict):
    id: ObjectId
    kind: Literal["list"]
    items: list[Value]


class TupleObject(TypedDict):
    id: ObjectId
    kind: Literal["tuple"]
    items: list[Value]


class SetObject(TypedDict):
    id: ObjectId
    kind: Literal["set"]
    items: list[Value]


class DictObject(TypedDict):
    id: ObjectId
    kind: Literal["dict"]
    entries: list[DictEntry]


class DequeObject(TypedDict):
    id: ObjectId
    kind: Literal["deque"]
    items: list[Value]
    maxlen: int | None


class InstanceObject(TypedDict):
    id: ObjectId
    kind: Literal["instance"]
    className: str
    attrs: list[Binding]


class OpaqueObject(TypedDict):
    id: ObjectId
    kind: Literal["opaque"]
    pythonType: str
    reason: Literal["unsupported_type", "unsupported_class", "builtin_symbol"]


ObjectRecord = (
    ListObject | TupleObject | DictObject | SetObject | DequeObject | InstanceObject | OpaqueObject
)


class Frame(TypedDict):
    id: FrameId
    parentId: FrameId | None
    kind: Literal["module", "function", "class"]
    name: str
    qualifiedName: str
    definitionLine: int | None
    callSite: SourceLocation | None
    parameterNames: list[str]
    localsSource: Literal["own", "globals"]
    locals: list[Binding]


class NormalizedState(TypedDict):
    version: int
    globals: list[Binding]
    frames: list[Frame]
    stack: list[FrameId]
    objects: list[ObjectRecord]
    eventRoots: list[Value]


class StateDelta(TypedDict):
    globals: list[Binding] | None
    stack: list[FrameId] | None
    eventRoots: list[Value] | None
    framesUpsert: list[Frame]
    framesRemove: list[FrameId]
    objectsUpsert: list[ObjectRecord]
    objectsRemove: list[ObjectId]


class Checkpoint(TypedDict):
    kind: Literal["checkpoint"]
    version: int
    state: NormalizedState


class Delta(TypedDict):
    kind: Literal["delta"]
    version: int
    baseVersion: int
    changes: StateDelta


StateRecord = Checkpoint | Delta


class ConsoleEntry(TypedDict):
    index: int
    kind: Literal["stdout", "stderr", "input"]
    text: str
    requestId: RequestId | None


class DiagnosticFrame(TypedDict):
    name: str
    source: SourceLocation


class Diagnostic(TypedDict):
    category: Literal["syntax", "runtime", "unsupported", "limit", "infrastructure"]
    code: ReasonCode | Literal["EXCEPTION_OBSERVED"]
    typeName: str | None
    message: str
    source: SourceLocation | None
    columnNumber: int | None
    traceback: list[DiagnosticFrame]
    omittedTracebackFrames: int


class Completion(TypedDict):
    outcome: Outcome
    reason: ReasonCode
    traceComplete: bool
    stateAccuracy: Literal["final", "last_observed", "unavailable"]
    primaryDiagnostic: Diagnostic | None
    acceptedAt: Timestamp
    finishedAt: Timestamp
    activeElapsedMs: float
    waitingElapsedMs: float
    exitCode: int | None
    cleanupConfirmed: bool


class ReturnKnown(TypedDict):
    returnValueKnown: Literal[True]
    returnValue: BoolValue | IntValue | FloatValue | StrValue | Ref


class ReturnUnknown(TypedDict):
    returnValueKnown: Literal[False]
    returnValue: None


class ExceptionPayload(TypedDict):
    diagnostic: Diagnostic


class EmptyPayload(TypedDict):
    pass


class EventBase(TypedDict):
    index: int
    stateVersion: int
    consoleOffset: int


class LineEvent(EventBase):
    kind: Literal["LINE"]
    frameId: FrameId
    source: SourceLocation
    payload: EmptyPayload


class CallEvent(EventBase):
    kind: Literal["CALL"]
    frameId: FrameId
    source: SourceLocation | None
    payload: EmptyPayload


class ReturnEvent(EventBase):
    kind: Literal["RETURN"]
    frameId: FrameId
    source: SourceLocation | None
    payload: ReturnKnown | ReturnUnknown


class ExceptionEvent(EventBase):
    kind: Literal["EXCEPTION"]
    frameId: FrameId
    source: SourceLocation | None
    payload: ExceptionPayload


class RunEndEvent(EventBase):
    kind: Literal["RUN_END"]
    frameId: None
    source: None
    payload: EmptyPayload


PythonTraceEvent = LineEvent | CallEvent | ReturnEvent | ExceptionEvent
TraceEvent = PythonTraceEvent | RunEndEvent


class TraceDocument(TypedDict):
    schemaVersion: Literal["1.0"]
    runId: RunId
    sourceCode: str
    sourceFileName: Literal["<codetrace-user>"]
    pythonVersion: Literal["3.13.16"]
    checkpointInterval: int
    limits: ResourceLimits
    events: list[TraceEvent]
    states: list[StateRecord]
    console: list[ConsoleEntry]
    completion: Completion


class TransportStateRecord(TypedDict):
    kind: Literal["state"]
    record: StateRecord


class TransportEventRecord(TypedDict):
    kind: Literal["event"]
    record: PythonTraceEvent


TransportTraceRecord = TransportStateRecord | TransportEventRecord


class SocketReadyPayload(TypedDict):
    limits: ResourceLimits
    apiVersion: Literal[1]


class WsStartPayload(TypedDict):
    clientRequestId: RunId
    code: str


class RunAcceptedPayload(TypedDict):
    clientRequestId: RunId
    acceptedAt: Timestamp
    status: Literal["STARTING"]


class RunRejectedPayload(TypedDict):
    clientRequestId: RunId
    code: ReasonCode | ApiValidationCode
    message: str


class StatusPayload(TypedDict):
    status: RunStatus


class ConsolePayload(TypedDict):
    entry: ConsoleEntry


class InputRequestPayload(TypedDict):
    requestId: RequestId
    prompt: str
    consoleOffset: int


class WsInputRequestPayload(InputRequestPayload):
    deadlineAt: Timestamp


class InputResponsePayload(TypedDict):
    requestId: RequestId
    text: str


class InputAcceptedPayload(TypedDict):
    requestId: RequestId


class InputRejectedPayload(TypedDict):
    requestId: RequestId
    code: Literal["INVALID_INPUT", "INPUT_ALREADY_ACCEPTED"]
    message: str


class RunFinishedPayload(TypedDict):
    completion: Completion
    traceAvailable: bool
    traceUrl: str | None
    traceBytes: int | None
    eventCount: int | None


class ProtocolErrorPayload(TypedDict):
    code: ApiValidationCode | Literal["CLIENT_PROTOCOL_ERROR"]
    message: str


class WsEnvelope[K: str, T, R: str | None](TypedDict):
    apiVersion: Literal[1]
    seq: int
    type: K
    runId: R
    payload: T


WsMessage = (
    WsEnvelope[Literal["socket_ready"], SocketReadyPayload, None]
    | WsEnvelope[Literal["start"], WsStartPayload, None]
    | WsEnvelope[Literal["run_accepted"], RunAcceptedPayload, str]
    | WsEnvelope[Literal["run_rejected"], RunRejectedPayload, None]
    | WsEnvelope[Literal["status"], StatusPayload, str]
    | WsEnvelope[Literal["console"], ConsolePayload, str]
    | WsEnvelope[Literal["input_request"], WsInputRequestPayload, str]
    | WsEnvelope[Literal["input_response"], InputResponsePayload, str]
    | WsEnvelope[Literal["input_accepted"], InputAcceptedPayload, str]
    | WsEnvelope[Literal["input_rejected"], InputRejectedPayload, str]
    | WsEnvelope[Literal["run_finished"], RunFinishedPayload, str]
    | WsEnvelope[Literal["protocol_error"], ProtocolErrorPayload, str | None]
)


class ReadyPayload(TypedDict):
    pythonVersion: Literal["3.13.16"]
    protocolVersion: Literal[1]


class RunnerStartPayload(TypedDict):
    code: str
    sourceFileName: Literal["<codetrace-user>"]
    limits: ResourceLimits
    checkpointInterval: int


class TraceChunkPayload(TypedDict):
    records: list[TransportTraceRecord]


class AbortRequestPayload(TypedDict):
    reason: LimitReasonCode | Literal["RUNNER_FAILURE"]
    diagnostic: Diagnostic


class RunnerSuccessPayload(TypedDict):
    outcome: Literal["SUCCESS"]
    reason: Literal["NONE"]
    primaryDiagnostic: None
    finalStateVersion: int


class RunnerUserErrorPayload(TypedDict):
    outcome: Literal["USER_ERROR"]
    reason: Literal["SYNTAX_ERROR", "UNCAUGHT_EXCEPTION", "UNSUPPORTED_FEATURE"]
    primaryDiagnostic: Diagnostic
    finalStateVersion: int


class FatalPayload(TypedDict):
    reason: Literal["RUNNER_FAILURE"]
    diagnostic: Diagnostic


class RunnerEnvelope[K: str, T](TypedDict):
    protocolVersion: Literal[1]
    runId: RunId
    seq: int
    type: K
    payload: T


RunnerMessage = (
    RunnerEnvelope[Literal["ready"], ReadyPayload]
    | RunnerEnvelope[Literal["start"], RunnerStartPayload]
    | RunnerEnvelope[Literal["trace_chunk"], TraceChunkPayload]
    | RunnerEnvelope[Literal["output"], ConsolePayload]
    | RunnerEnvelope[Literal["input_request"], InputRequestPayload]
    | RunnerEnvelope[Literal["input_response"], InputResponsePayload]
    | RunnerEnvelope[Literal["abort_request"], AbortRequestPayload]
    | RunnerEnvelope[Literal["finished"], RunnerSuccessPayload | RunnerUserErrorPayload]
    | RunnerEnvelope[Literal["fatal"], FatalPayload]
)
