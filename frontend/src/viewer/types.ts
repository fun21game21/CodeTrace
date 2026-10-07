import type {
  SelectedStepView,
  StepRequest,
  TraceCore,
} from "../trace/index.ts";
import type { VisualizerConfig } from "../visualizers/types.ts";

export const SPEEDS = Object.freeze([1, 2, 5, 10, 20] as const);
export type Speed = (typeof SPEEDS)[number];
export interface TraceSelectionExecutor {
  select(request: StepRequest): Promise<SelectedStepView>;
}
export interface Scheduler {
  schedule(callback: () => void, delayMs: number): unknown;
  cancel(token: unknown): void;
}
export interface ViewerOptions {
  scheduler?: Scheduler;
  executor?: (core: TraceCore) => TraceSelectionExecutor;
}
export interface ViewerSnapshot {
  readonly phase: "EMPTY" | "ATTACHED" | "READY";
  readonly runId: string | null;
  readonly totalSteps: number;
  readonly committedStep: number | null;
  readonly pendingStep: number | null;
  readonly latestRequestId: number;
  readonly playing: boolean;
  readonly speed: Speed;
  readonly selectedFrameId: string | null;
  readonly committedView: SelectedStepView | null;
  readonly directStepDraft: string;
  readonly stepValidationMessage: string | null;
  readonly selectionError: string | null;
  readonly sliderTarget: number | null;
  readonly visualizerConfig: VisualizerConfig;
}
