import type {
  Binding,
  Completion,
  ConsoleEntry,
  Diagnostic,
  Frame,
  NormalizedState,
  ObjectRecord,
  ObjectId,
  TraceDocument,
  TraceEvent,
  Value,
} from "../../../contracts/typescript/index.ts";

export type Immutable<T> = T extends readonly (infer U)[]
  ? readonly Immutable<U>[]
  : T extends object
    ? { readonly [K in keyof T]: Immutable<T[K]> }
    : T;
export type Trace = Immutable<TraceDocument>;
export type State = Immutable<NormalizedState>;
export type StoredFrame = Immutable<Frame>;
export type StoredObject = Immutable<ObjectRecord>;
export type StoredValue = Immutable<Value>;
export type Bindings = Immutable<Binding[]>;
export interface StepRequest {
  readonly step: number;
  readonly requestId: number;
  /** undefined: top frame; null: explicit globals; absent ID: top fallback. */
  readonly requestedFrameId?: string | null;
}
export interface Highlight {
  readonly lineNumber: number | null;
  readonly mode: "before_line" | "event" | "error" | "none";
}
export interface SelectedStepView {
  readonly runId: string;
  readonly requestId: number;
  readonly step: number;
  readonly totalSteps: number;
  readonly event: Immutable<TraceEvent>;
  readonly state: State;
  readonly selectedFrameId: string | null;
  readonly globals: Bindings;
  readonly selectedLocals: Bindings;
  readonly callStack: readonly StoredFrame[];
  readonly consoleEntries: Immutable<ConsoleEntry[]>;
  /** Union of changed names in globals and selected scope, in code-point order. */
  readonly changedBindingNames: readonly string[];
  readonly changedObjectIds: readonly string[];
  /** VISUALIZERS approved presentation metadata, relative to the preceding trace Event. */
  readonly arrayPositionChanges: readonly Readonly<{
    objectId: ObjectId;
    indices: readonly number[];
  }>[];
  readonly highlight: Highlight;
  readonly eventDiagnostic: Immutable<Diagnostic> | null;
  readonly completion: Immutable<Completion> | null;
}
