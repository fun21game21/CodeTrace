/** Manually maintained DTOs. Validators perform runtime checks without coercion. */
export type RunId = string;
export type ObjectId = string;
export type FrameId = string;
export type RequestId = string;
export type Timestamp = string;
export type LimitReasonCode =
  | "ACTIVE_TIMEOUT"
  | "INPUT_IDLE_TIMEOUT"
  | "CONTAINER_LIFETIME"
  | "STARTUP_TIMEOUT"
  | "MEMORY_LIMIT"
  | "EVENT_LIMIT"
  | "TRACE_BYTES_LIMIT"
  | "REGISTRY_LIMIT"
  | "STATE_BYTES_LIMIT"
  | "COLLECTION_LIMIT"
  | "STRING_LIMIT"
  | "INT_DIGITS_LIMIT"
  | "CONSOLE_LIMIT"
  | "FRAME_LIMIT"
  | "INPUT_REQUEST_LIMIT";
export type ReasonCode =
  | "NONE"
  | "SYNTAX_ERROR"
  | "UNCAUGHT_EXCEPTION"
  | "UNSUPPORTED_FEATURE"
  | LimitReasonCode
  | "DOCKER_UNAVAILABLE"
  | "IMAGE_UNAVAILABLE"
  | "CLIENT_PROTOCOL_ERROR"
  | "RUNNER_PROTOCOL_ERROR"
  | "RUNNER_FAILURE"
  | "TRACE_RECORDER_FAILURE"
  | "FINALIZATION_TIMEOUT"
  | "CLEANUP_FAILED"
  | "CONNECTION_LOST"
  | "BACKEND_SHUTDOWN";
export type ApiValidationCode =
  | "RUN_BUSY"
  | "INVALID_START"
  | "CODE_TOO_LARGE"
  | "INVALID_INPUT"
  | "INPUT_ALREADY_ACCEPTED"
  | "RESULT_NOT_AVAILABLE";
export type RunStatus =
  | "STARTING"
  | "RUNNING"
  | "WAITING_INPUT"
  | "FINALIZING"
  | "SUCCEEDED"
  | "FAILED"
  | "LIMIT_EXCEEDED"
  | "INTERRUPTED";
export type Outcome =
  | "SUCCESS"
  | "USER_ERROR"
  | "LIMIT_EXCEEDED"
  | "SYSTEM_ERROR"
  | "INTERRUPTED";
export interface ResourceLimits {
  active_runs: 1;
  retained_results: 1;
  code_bytes: number;
  max_events: number;
  trace_bytes: number;
  terminal_reserve_bytes: number;
  registry_objects: number;
  state_objects: number;
  state_bytes: number;
  collection_items: number;
  instance_attributes: number;
  string_bytes: number;
  integer_decimal_digits: number;
  active_frames: number;
  console_bytes: number;
  output_chunk_bytes: number;
  input_value_bytes: number;
  input_requests: number;
  checkpoint_interval: number;
  active_timeout_ms: number;
  input_idle_timeout_ms: number;
  container_lifetime_ms: number;
  startup_timeout_ms: number;
  result_ttl_ms: number;
  cleanup_command_timeout_ms: number;
  cleanup_attempts: number;
  finalization_timeout_ms: number;
  watchdog_tick_ms: number;
  memory_bytes: number;
  memory_plus_swap_bytes: number;
  cpus: number;
  pids_limit: number;
  tmpfs_bytes: number;
  runner_message_bytes: number;
  ws_message_bytes: number;
  native_stderr_bytes: number;
  history_entries: number;
  max_visual_nodes: number;
  visible_array_items: number;
}
export interface SourceLocation {
  fileName: "<codetrace-user>";
  lineNumber: number | null;
}
export interface NoneValue {
  kind: "scalar";
  type: "none";
  value: null;
}
export interface BoolValue {
  kind: "scalar";
  type: "bool";
  value: boolean;
}
export interface IntValue {
  kind: "scalar";
  type: "int";
  value: string;
}
export interface FloatValue {
  kind: "scalar";
  type: "float";
  value: string;
}
export interface StrValue {
  kind: "scalar";
  type: "str";
  value: string;
}
export interface Ref {
  $ref: ObjectId;
}
export type Value =
  | NoneValue
  | BoolValue
  | IntValue
  | FloatValue
  | StrValue
  | Ref;
export interface Binding {
  name: string;
  value: Value;
}
export interface DictEntry {
  key: Value;
  value: Value;
}
export interface ListObject {
  id: ObjectId;
  kind: "list";
  items: Value[];
}
export interface TupleObject {
  id: ObjectId;
  kind: "tuple";
  items: Value[];
}
export interface SetObject {
  id: ObjectId;
  kind: "set";
  items: Value[];
}
export interface DictObject {
  id: ObjectId;
  kind: "dict";
  entries: DictEntry[];
}
export interface DequeObject {
  id: ObjectId;
  kind: "deque";
  items: Value[];
  maxlen: number | null;
}
export interface InstanceObject {
  id: ObjectId;
  kind: "instance";
  className: string;
  attrs: Binding[];
}
export interface OpaqueObject {
  id: ObjectId;
  kind: "opaque";
  pythonType: string;
  reason: "unsupported_type" | "unsupported_class" | "builtin_symbol";
}
export type ObjectRecord =
  | ListObject
  | TupleObject
  | SetObject
  | DictObject
  | DequeObject
  | InstanceObject
  | OpaqueObject;
export interface Frame {
  id: FrameId;
  parentId: FrameId | null;
  kind: "module" | "function" | "class";
  name: string;
  qualifiedName: string;
  definitionLine: number | null;
  callSite: SourceLocation | null;
  parameterNames: string[];
  localsSource: "own" | "globals";
  locals: Binding[];
}
export interface NormalizedState {
  version: number;
  globals: Binding[];
  frames: Frame[];
  stack: FrameId[];
  objects: ObjectRecord[];
  eventRoots: Value[];
}
export interface StateDelta {
  globals: Binding[] | null;
  stack: FrameId[] | null;
  eventRoots: Value[] | null;
  framesUpsert: Frame[];
  framesRemove: FrameId[];
  objectsUpsert: ObjectRecord[];
  objectsRemove: ObjectId[];
}
export interface Checkpoint {
  kind: "checkpoint";
  version: number;
  state: NormalizedState;
}
export interface Delta {
  kind: "delta";
  version: number;
  baseVersion: number;
  changes: StateDelta;
}
export type StateRecord = Checkpoint | Delta;
export interface ConsoleEntry {
  index: number;
  kind: "stdout" | "stderr" | "input";
  text: string;
  requestId: RequestId | null;
}
export interface DiagnosticFrame {
  name: string;
  source: SourceLocation;
}
export interface Diagnostic {
  category: "syntax" | "runtime" | "unsupported" | "limit" | "infrastructure";
  code: ReasonCode | "EXCEPTION_OBSERVED";
  typeName: string | null;
  message: string;
  source: SourceLocation | null;
  columnNumber: number | null;
  traceback: DiagnosticFrame[];
  omittedTracebackFrames: number;
}
export interface Completion {
  outcome: Outcome;
  reason: ReasonCode;
  traceComplete: boolean;
  stateAccuracy: "final" | "last_observed" | "unavailable";
  primaryDiagnostic: Diagnostic | null;
  acceptedAt: Timestamp;
  finishedAt: Timestamp;
  activeElapsedMs: number;
  waitingElapsedMs: number;
  exitCode: number | null;
  cleanupConfirmed: boolean;
}
export interface ReturnKnown {
  returnValueKnown: true;
  returnValue: Exclude<Value, NoneValue>;
}
export interface ReturnUnknown {
  returnValueKnown: false;
  returnValue: null;
}
export interface ExceptionPayload {
  diagnostic: Diagnostic;
}
export type EmptyPayload = Record<string, never>;
export interface EventBase {
  index: number;
  stateVersion: number;
  consoleOffset: number;
}
export interface LineEvent extends EventBase {
  kind: "LINE";
  frameId: FrameId;
  source: SourceLocation & { lineNumber: number };
  payload: EmptyPayload;
}
export interface CallEvent extends EventBase {
  kind: "CALL";
  frameId: FrameId;
  source: SourceLocation | null;
  payload: EmptyPayload;
}
export interface ReturnEvent extends EventBase {
  kind: "RETURN";
  frameId: FrameId;
  source: SourceLocation | null;
  payload: ReturnKnown | ReturnUnknown;
}
export interface ExceptionEvent extends EventBase {
  kind: "EXCEPTION";
  frameId: FrameId;
  source: SourceLocation | null;
  payload: ExceptionPayload;
}
export interface RunEndEvent extends EventBase {
  kind: "RUN_END";
  frameId: null;
  source: null;
  payload: EmptyPayload;
}
export type PythonTraceEvent =
  | LineEvent
  | CallEvent
  | ReturnEvent
  | ExceptionEvent;
export type TraceEvent = PythonTraceEvent | RunEndEvent;
export interface TraceDocument {
  schemaVersion: "1.0";
  runId: RunId;
  sourceCode: string;
  sourceFileName: "<codetrace-user>";
  pythonVersion: "3.13.16";
  checkpointInterval: number;
  limits: ResourceLimits;
  events: TraceEvent[];
  states: StateRecord[];
  console: ConsoleEntry[];
  completion: Completion;
}
export type TransportTraceRecord =
  | { kind: "state"; record: StateRecord }
  | { kind: "event"; record: PythonTraceEvent };
export interface SocketReadyPayload {
  limits: ResourceLimits;
  apiVersion: 1;
}
export interface WsStartPayload {
  clientRequestId: RunId;
  code: string;
}
export interface RunAcceptedPayload {
  clientRequestId: RunId;
  acceptedAt: Timestamp;
  status: "STARTING";
}
export interface RunRejectedPayload {
  clientRequestId: RunId;
  code: ReasonCode | ApiValidationCode;
  message: string;
}
export interface StatusPayload {
  status: RunStatus;
}
export interface ConsolePayload {
  entry: ConsoleEntry;
}
export interface InputRequestPayload {
  requestId: RequestId;
  prompt: string;
  consoleOffset: number;
}
export interface WsInputRequestPayload extends InputRequestPayload {
  deadlineAt: Timestamp;
}
export interface InputResponsePayload {
  requestId: RequestId;
  text: string;
}
export interface InputAcceptedPayload {
  requestId: RequestId;
}
export interface InputRejectedPayload {
  requestId: RequestId;
  code: "INVALID_INPUT" | "INPUT_ALREADY_ACCEPTED";
  message: string;
}
export interface RunFinishedPayload {
  completion: Completion;
  traceAvailable: boolean;
  traceUrl: string | null;
  traceBytes: number | null;
  eventCount: number | null;
}
export interface ProtocolErrorPayload {
  code: ApiValidationCode | "CLIENT_PROTOCOL_ERROR";
  message: string;
}
export interface WsEnvelope<K extends string, P, R extends string | null> {
  apiVersion: 1;
  seq: number;
  type: K;
  runId: R;
  payload: P;
}
export type WsMessage =
  | WsEnvelope<"socket_ready", SocketReadyPayload, null>
  | WsEnvelope<"start", WsStartPayload, null>
  | WsEnvelope<"run_accepted", RunAcceptedPayload, RunId>
  | WsEnvelope<"run_rejected", RunRejectedPayload, null>
  | WsEnvelope<"status", StatusPayload, RunId>
  | WsEnvelope<"console", ConsolePayload, RunId>
  | WsEnvelope<"input_request", WsInputRequestPayload, RunId>
  | WsEnvelope<"input_response", InputResponsePayload, RunId>
  | WsEnvelope<"input_accepted", InputAcceptedPayload, RunId>
  | WsEnvelope<"input_rejected", InputRejectedPayload, RunId>
  | WsEnvelope<"run_finished", RunFinishedPayload, RunId>
  | WsEnvelope<"protocol_error", ProtocolErrorPayload, RunId | null>;
export interface ReadyPayload {
  pythonVersion: "3.13.16";
  protocolVersion: 1;
}
export interface RunnerStartPayload {
  code: string;
  sourceFileName: "<codetrace-user>";
  limits: ResourceLimits;
  checkpointInterval: number;
}
export interface TraceChunkPayload {
  records: TransportTraceRecord[];
}
export interface AbortRequestPayload {
  reason: LimitReasonCode | "RUNNER_FAILURE";
  diagnostic: Diagnostic;
}
export interface RunnerSuccessPayload {
  outcome: "SUCCESS";
  reason: "NONE";
  primaryDiagnostic: null;
  finalStateVersion: number;
}
export interface RunnerUserErrorPayload {
  outcome: "USER_ERROR";
  reason: "SYNTAX_ERROR" | "UNCAUGHT_EXCEPTION" | "UNSUPPORTED_FEATURE";
  primaryDiagnostic: Diagnostic;
  finalStateVersion: number;
}
export interface FatalPayload {
  reason: "RUNNER_FAILURE";
  diagnostic: Diagnostic;
}
export interface RunnerEnvelope<K extends string, P> {
  protocolVersion: 1;
  runId: RunId;
  seq: number;
  type: K;
  payload: P;
}
export type RunnerMessage =
  | RunnerEnvelope<"ready", ReadyPayload>
  | RunnerEnvelope<"start", RunnerStartPayload>
  | RunnerEnvelope<"trace_chunk", TraceChunkPayload>
  | RunnerEnvelope<"output", ConsolePayload>
  | RunnerEnvelope<"input_request", InputRequestPayload>
  | RunnerEnvelope<"input_response", InputResponsePayload>
  | RunnerEnvelope<"abort_request", AbortRequestPayload>
  | RunnerEnvelope<"finished", RunnerSuccessPayload | RunnerUserErrorPayload>
  | RunnerEnvelope<"fatal", FatalPayload>;
