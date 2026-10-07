import type { Completion } from "../../../contracts/typescript/index.ts";
import type {
  Immutable,
  SelectedStepView,
  StepRequest,
} from "../trace/index.ts";
import type { TraceLoadRequest } from "./types.ts";
export type Command =
  | {
      type: "load";
      sessionId: string;
      request: TraceLoadRequest;
      measure?: true;
    }
  | { type: "select"; sessionId: string; request: StepRequest }
  | { type: "dispose"; sessionId: string };
export interface LoadTiming {
  fetchMs: number;
  jsonParseMs: number;
  traceCoreLoadMs: number;
}
export type Reply =
  | {
      type: "loaded";
      sessionId: string;
      runId: string;
      sourceCode: string;
      totalSteps: number;
      validated: true;
      completion: Immutable<Completion>;
      timing?: LoadTiming;
    }
  | {
      type: "selected";
      sessionId: string;
      requestId: number;
      selectionMs?: number;
      view: SelectedStepView;
    }
  | {
      type: "error";
      sessionId: string;
      requestId: number | null;
      code: "LOAD_FAILED" | "SELECT_FAILED" | "PROTOCOL_ERROR";
      message: string;
    };
export interface WorkerPort {
  postMessage(message: Command): void;
  addEventListener(
    type: "message",
    handler: (event: { data: Reply }) => void,
  ): void;
  addEventListener(type: "error" | "messageerror", handler: () => void): void;
  terminate(): void;
}
