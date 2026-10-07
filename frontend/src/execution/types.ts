import type { Completion } from "../../../contracts/typescript/index.ts";
import type { Immutable, TraceCore } from "../trace/index.ts";
import type { TraceSelectionExecutor } from "../viewer/types.ts";
export interface TraceLoadRequest {
  readonly traceUrl: string;
  readonly expectedRunId: string;
  readonly expectedSourceCode: string;
  readonly expectedEventCount: number | null;
}
export interface LoadedTraceSession {
  readonly identity: string;
  readonly runId: string;
  readonly sourceCode: string;
  readonly totalSteps: number;
  readonly completion: Immutable<Completion>;
  readonly executor: TraceSelectionExecutor;
  dispose(): void;
}
export type TraceHandle = TraceCore | LoadedTraceSession;
export interface TraceLoader {
  load(request: TraceLoadRequest): Promise<LoadedTraceSession>;
  dispose(): void;
}
