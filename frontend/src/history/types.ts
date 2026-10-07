import type { RunStatus } from "../../../contracts/typescript/index.ts";

export interface HistoryEntry {
  readonly id: string;
  readonly code: string;
  readonly createdAt: string;
  readonly lastRunAt: string;
  readonly status: RunStatus;
}
/** In-memory ownership only; never passed to objectStore.put. */
export interface HistoryAssociation {
  readonly historyId: string;
  readonly runId: string;
  readonly acceptedAt: string;
  readonly code: string;
}
export interface AcceptedSource {
  readonly code: string;
  readonly acceptedAt: string;
}
export interface HistorySnapshot {
  readonly phase: "LOADING" | "READY" | "UNAVAILABLE";
  readonly entries: readonly HistoryEntry[];
  readonly warning: string | null;
}
export interface HistoryStore {
  open(): Promise<void>;
  list(): Promise<readonly HistoryEntry[]>;
  recordAccepted(source: AcceptedSource): Promise<HistoryEntry>;
  updateStatus(owner: HistoryAssociation, status: RunStatus): Promise<boolean>;
  delete(id: string): Promise<void>;
  markStaleActiveInterrupted(): Promise<void>;
  onUnavailable(listener: (error: unknown) => void): () => void;
  close(): void;
}
