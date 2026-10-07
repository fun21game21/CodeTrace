import { IDBFactory } from "fake-indexeddb";
import type {
  HistoryAssociation,
  HistoryEntry,
  HistoryStore,
  AcceptedSource,
} from "../../src/history/types.ts";
import type { RunStatus } from "../../../contracts/typescript/index.ts";
import {
  IndexedDbHistoryStore,
  HISTORY_DB,
} from "../../src/history/indexeddb.ts";
export const T1 = "2026-10-07T10:00:00.000001Z",
  T2 = "2026-10-07T10:00:00.000002Z";
export const owner = (
  entry: HistoryEntry,
  runId = crypto.randomUUID(),
): HistoryAssociation => ({
  historyId: entry.id,
  runId,
  code: entry.code,
  acceptedAt: entry.lastRunAt,
});
export async function raw(
  factory: IDBFactory,
  version = 1,
): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const r = factory.open(HISTORY_DB, version);
    r.onsuccess = () => resolve(r.result);
    r.onerror = () => reject(r.error);
  });
}
export async function putRaw(
  factory: IDBFactory,
  value: unknown,
): Promise<void> {
  const db = await raw(factory);
  await new Promise<void>((resolve, reject) => {
    const tx = db.transaction("entries", "readwrite");
    tx.objectStore("entries").put(value);
    tx.oncomplete = () => resolve();
    tx.onabort = () => reject(tx.error);
  });
  db.close();
}
export async function fresh() {
  const factory = new IDBFactory();
  let database: IDBDatabase | null = null;
  const modes: IDBTransactionMode[] = [];
  let fault: "abort" | "quota" | "put" | "delete" | null = null;
  const open = factory.open.bind(factory);
  factory.open = (...args) => {
    const r = open(...args);
    r.addEventListener("success", () => {
      const db = r.result;
      database = db;
      const transaction = db.transaction.bind(db);
      db.transaction = (...txArgs) => {
        modes.push(txArgs[1] ?? "readonly");
        const tx = transaction(...txArgs);
        if (fault && txArgs[1] === "readwrite") {
          const kind = fault;
          fault = null;
          if (kind === "abort") queueMicrotask(() => tx.abort());
          else {
            const store = tx.objectStore("entries");
            if (kind === "delete")
              store.delete = () => {
                throw new DOMException("delete rejected", "UnknownError");
              };
            else
              store.put = () => {
                throw new DOMException(
                  "write rejected",
                  kind === "quota" ? "QuotaExceededError" : "UnknownError",
                );
              };
          }
        }
        return tx;
      };
    });
    return r;
  };
  const store = new IndexedDbHistoryStore(factory);
  await store.open();
  modes.length = 0;
  return {
    factory,
    store,
    modes,
    get db() {
      return database!;
    },
    inject(kind: typeof fault) {
      fault = kind;
    },
  };
}
export async function drain(turns = 30) {
  for (let i = 0; i < turns; i++)
    await new Promise<void>((r) => setImmediate(r));
}
export async function until(check: () => boolean) {
  const end = Date.now() + 5000;
  while (!check()) {
    if (Date.now() > end) throw Error("HISTORY test deadline");
    await drain(1);
  }
}
/** Controllable repository port to test lifecycle timing independently of IDB task scheduling. */
export class DelayedStore implements HistoryStore {
  entries: HistoryEntry[] = [];
  updates: { owner: HistoryAssociation; status: RunStatus }[] = [];
  accepts: AcceptedSource[] = [];
  pending: ((entry: HistoryEntry) => void)[] = [];
  recoveries = 0;
  closed = false;
  failAccept = false;
  failStatus = false;
  failDelete = false;
  failOpen = false;
  async open() {
    if (this.failOpen) throw Error("open failed");
  }
  async list() {
    return this.entries;
  }
  recordAccepted(source: AcceptedSource): Promise<HistoryEntry> {
    this.accepts.push(source);
    if (this.failAccept) return Promise.reject(Error("accepted failed"));
    return new Promise((resolve) =>
      this.pending.push((entry) => {
        this.entries = this.entries
          .filter((e) => e.id !== entry.id)
          .concat(entry);
        resolve(entry);
      }),
    );
  }
  finish(index = 0) {
    const source = this.accepts[index]!;
    const previous = this.entries.find((e) => e.code === source.code);
    this.pending[index]!({
      id: previous?.id ?? crypto.randomUUID(),
      code: source.code,
      createdAt: previous?.createdAt ?? source.acceptedAt,
      lastRunAt: source.acceptedAt,
      status: "STARTING",
    });
  }
  async updateStatus(o: HistoryAssociation, s: RunStatus) {
    this.updates.push({ owner: o, status: s });
    if (this.failStatus) throw Error("status failed");
    const i = this.entries.findIndex(
      (e) =>
        e.id === o.historyId &&
        e.code === o.code &&
        e.lastRunAt === o.acceptedAt,
    );
    if (i < 0) return false;
    this.entries[i] = { ...this.entries[i]!, status: s };
    return true;
  }
  async delete(id: string) {
    if (this.failDelete) throw Error("delete failed");
    this.entries = this.entries.filter((e) => e.id !== id);
  }
  async markStaleActiveInterrupted() {
    this.recoveries++;
  }
  onUnavailable() {
    return () => {};
  }
  close() {
    this.closed = true;
  }
}
