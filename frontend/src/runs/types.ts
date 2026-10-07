import type {
  Completion,
  ConsoleEntry,
  ResourceLimits,
  RunStatus,
  WsInputRequestPayload,
} from "../../../contracts/typescript/index.ts";
import type { Immutable, TraceCore } from "../trace/index.ts";

import type { LoadedTraceSession, TraceLoader } from "../execution/types.ts";

export interface Health {
  readonly apiVersion: 1;
  readonly available: boolean;
  readonly reason: string | null;
  readonly pythonVersion: "3.13.16";
  readonly limits: Immutable<ResourceLimits>;
}
export interface PendingInput extends WsInputRequestPayload {
  readonly text: string;
  readonly submitting: boolean;
  readonly error: string | null;
}
export interface RunState {
  readonly health: Health | null;
  readonly healthPhase: "LOADING" | "READY" | "FAILED";
  readonly healthError: string | null;
  readonly editor: {
    readonly draftCode: string;
    readonly mode: "editing" | "run_source";
    readonly runSourceCode: string | null;
  };
  readonly active: {
    readonly runId: string | null;
    readonly clientRequestId: string | null;
    readonly status: RunStatus | "IDLE";
    readonly sourceCode: string | null;
    readonly pendingInput: Readonly<PendingInput> | null;
    readonly liveConsole: readonly Immutable<ConsoleEntry>[];
    readonly completion: Immutable<Completion> | null;
    readonly transportLost: boolean;
  };
  readonly trace: {
    readonly phase: "EMPTY" | "LOADING" | "READY" | "LOAD_FAILED";
    readonly handle: TraceCore | null;
    /** Mutually exclusive with the legacy direct handle. Worker owns its full trace. */
    readonly session: LoadedTraceSession | null;
    readonly runId: string | null;
    readonly eventCount: number | null;
    readonly loadError: string | null;
    readonly releasePending: boolean;
    readonly releaseError: string | null;
  };
  readonly connecting: boolean;
  readonly transportError: string | null;
}
export interface SocketPort {
  onMessage(handler: (data: unknown) => void): void;
  onClose(handler: () => void): void;
  onError(handler: () => void): void;
  send(text: string): void;
  close(): void;
}
export interface HttpResult {
  readonly status: number;
  json(): Promise<unknown>;
}
export interface RunDependencies {
  traceLoader?: TraceLoader;
  fetch(url: string, options?: { method: "POST" }): Promise<HttpResult>;
  socket(): SocketPort;
  uuid(): string;
  handshakeTimeoutMs?: number;
}

/** Frontend lifecycle notifications; no changes to WebSocket contracts. */
export interface AcceptedRun {
  readonly runId: string;
  readonly sourceCode: string;
  readonly acceptedAt: string;
}
export type RunLifecycleEvent =
  | { readonly type: "accepted"; readonly run: AcceptedRun }
  | {
      readonly type: "status";
      readonly runId: string;
      readonly status: RunStatus;
    }
  | { readonly type: "reset" }
  | { readonly type: "dispose" };
