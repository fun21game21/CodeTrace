import type { RunStatus } from "../../../contracts/typescript/index.ts";
import type {
  HistoryStore,
  HistorySnapshot,
  HistoryAssociation,
  HistoryEntry,
} from "./types.ts";
import { compareHistory } from "./values.ts";
import type { AcceptedRun } from "../runs/types.ts";
interface PendingRun {
  readonly run: AcceptedRun;
  status: RunStatus;
  owner: HistoryAssociation | null;
  failed: boolean;
  writing: boolean;
  revision: number;
}
/** Browser persistence is an independent side effect, never a Run admission gate. */
export class HistoryController {
  private state: HistorySnapshot = Object.freeze({
    phase: "LOADING",
    entries: Object.freeze([]),
    warning: null,
  });
  private listeners = new Set<() => void>();
  private runs = new Map<string, PendingRun>();
  private opening: Promise<void> | null = null;
  private disposed = false;
  private unavailableUnsubscribe: () => void;
  private refreshGeneration = 0;
  private readonly store: HistoryStore;
  private readonly hasActiveConnection: () => boolean;
  constructor(
    store: HistoryStore,
    hasActiveConnection: () => boolean = () => false,
  ) {
    this.store = store;
    this.hasActiveConnection = hasActiveConnection;
    this.unavailableUnsubscribe = store.onUnavailable((error) =>
      this.fail(error, true),
    );
  }
  getSnapshot = (): HistorySnapshot => this.state;
  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };
  private publish(patch: Partial<HistorySnapshot>): void {
    if (this.disposed) return;
    this.state = Object.freeze({ ...this.state, ...patch });
    for (const listener of this.listeners) listener();
  }
  private fail(error: unknown, unavailable = false): void {
    if (unavailable) ++this.refreshGeneration;
    this.publish({
      ...(unavailable ? { phase: "UNAVAILABLE" as const } : {}),
      warning:
        "История недоступна или не сохранена. Запуск доступен. " +
        (error instanceof Error
          ? error.name + ": " + error.message
          : "Ошибка IndexedDB."),
    });
  }
  initialize(): Promise<void> {
    if (this.opening) return this.opening;
    this.publish({ phase: "LOADING" });
    this.opening = (async () => {
      try {
        await this.store.open();
        // Recheck immediately before creating the recovery transaction, including runtime reopen.
        if (!this.hasActiveConnection())
          await this.store.markStaleActiveInterrupted();
        await this.refresh();
      } catch (error) {
        this.fail(error, true);
      }
    })();
    const attempt = this.opening;
    void attempt.finally(() => {
      if (this.opening === attempt) this.opening = null;
    });
    return attempt;
  }
  private async refresh(): Promise<void> {
    const generation = ++this.refreshGeneration;
    try {
      const entries = await this.store.list();
      if (generation === this.refreshGeneration)
        this.publish({
          phase: "READY",
          entries: Object.freeze([...entries].sort(compareHistory)),
        });
    } catch (error) {
      this.fail(error, true);
    }
  }
  accepted(run: AcceptedRun): void {
    if (this.disposed || this.runs.has(run.runId)) return;
    const pending: PendingRun = {
      run: Object.freeze({ ...run }),
      status: "STARTING",
      owner: null,
      failed: false,
      writing: false,
      revision: 0,
    };
    this.runs.set(run.runId, pending);
    void (async () => {
      try {
        if (this.state.phase === "LOADING")
          await (this.opening ?? this.initialize());
        if (this.state.phase !== "READY" || this.disposed) {
          pending.failed = true;
          return;
        }
        const entry = await this.store.recordAccepted({
          code: pending.run.sourceCode,
          acceptedAt: pending.run.acceptedAt,
        });
        pending.owner = Object.freeze({
          historyId: entry.id,
          runId: run.runId,
          code: pending.run.sourceCode,
          acceptedAt: pending.run.acceptedAt,
        });
        await this.refresh();
        void this.flush(pending);
      } catch (error) {
        pending.failed = true;
        this.fail(error);
      }
    })();
  }
  status(runId: string, status: RunStatus): void {
    const pending = this.runs.get(runId);
    if (!pending || pending.failed || this.disposed) return;
    pending.status = status;
    ++pending.revision;
    if (pending.owner) void this.flush(pending);
  }
  private async flush(pending: PendingRun): Promise<void> {
    if (pending.writing || !pending.owner || pending.failed || this.disposed)
      return;
    pending.writing = true;
    try {
      // At most one status transaction per association; coalesce events during each write.
      let revision: number;
      do {
        revision = pending.revision;
        await this.store.updateStatus(pending.owner, pending.status);
        await this.refresh();
      } while (revision !== pending.revision && !this.disposed);
    } catch (error) {
      pending.failed = true;
      this.fail(error);
    } finally {
      pending.writing = false;
    }
  }
  /** Forget routing on editor reset; in-flight closures retain their original guarded owner. */
  reset(): void {
    this.runs.clear();
  }
  async delete(id: string): Promise<boolean> {
    if (this.state.phase !== "READY" || this.disposed) return false;
    try {
      await this.store.delete(id);
      await this.refresh();
      return true;
    } catch (error) {
      this.fail(error);
      return false;
    }
  }
  entry(id: string): HistoryEntry | undefined {
    return this.state.entries.find((e) => e.id === id);
  }
  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    this.runs.clear();
    this.unavailableUnsubscribe();
    this.store.close();
    this.listeners.clear();
  }
}
