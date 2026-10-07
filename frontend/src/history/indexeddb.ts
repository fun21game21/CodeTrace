import { defaultLimits } from "../../../contracts/typescript/index.ts";
import type { RunStatus } from "../../../contracts/typescript/index.ts";
import type {
  HistoryEntry,
  HistoryStore,
  AcceptedSource,
  HistoryAssociation,
} from "./types.ts";
import {
  ACTIVE_STATUSES,
  compareHistory,
  historyEntry,
  validTimestamp,
  validStatus,
} from "./values.ts";
export const HISTORY_DB = "codetrace-history";
export const HISTORY_VERSION = 1;
export const HISTORY_STORE = "entries";

/** Native requests only inside transaction callbacks: no await while a transaction is live. */
export class IndexedDbHistoryStore implements HistoryStore {
  private db: IDBDatabase | null = null;
  private opening: Promise<void> | null = null;
  private generation = 0;
  private listeners = new Set<(error: unknown) => void>();
  private readonly factory: IDBFactory | undefined;
  private readonly uuid: () => string;
  constructor(
    factory: IDBFactory | undefined = globalThis.indexedDB,
    uuid: () => string = () => crypto.randomUUID(),
  ) {
    this.factory = factory;
    this.uuid = uuid;
  }
  onUnavailable(listener: (error: unknown) => void): () => void {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }
  open(): Promise<void> {
    if (this.db) return Promise.resolve();
    if (this.opening) return this.opening;
    const generation = this.generation;
    const attempt = new Promise<void>((resolve, reject) => {
      if (!this.factory) {
        reject(new Error("IndexedDB недоступна."));
        return;
      }
      const request = this.factory.open(HISTORY_DB, HISTORY_VERSION);
      let failed = false;
      const fail = (error: unknown) => {
        failed = true;
        reject(error);
      };
      request.onblocked = () =>
        fail(new Error("Открытие истории заблокировано другой вкладкой."));
      request.onerror = () =>
        fail(request.error ?? new Error("Не удалось открыть историю."));
      request.onupgradeneeded = () => {
        const db = request.result;
        const entries = db.objectStoreNames.contains(HISTORY_STORE)
          ? request.transaction!.objectStore(HISTORY_STORE)
          : db.createObjectStore(HISTORY_STORE, { keyPath: "id" });
        for (const name of ["lastRunAt", "createdAt"])
          if (!entries.indexNames.contains(name))
            entries.createIndex(name, name, { unique: false });
      };
      request.onsuccess = () => {
        const db = request.result;
        if (failed || generation !== this.generation) {
          db.close();
          fail(new Error("Открытие истории отменено."));
          return;
        }
        try {
          const store = db
            .transaction(HISTORY_STORE, "readonly")
            .objectStore(HISTORY_STORE);
          if (
            db.version !== 1 ||
            db.objectStoreNames.length !== 1 ||
            store.keyPath !== "id" ||
            store.autoIncrement ||
            store.indexNames.length !== 2 ||
            ["lastRunAt", "createdAt"].some((name) => {
              const index = store.index(name);
              return index.keyPath !== name || index.unique || index.multiEntry;
            })
          )
            throw new Error("Неверная схема истории v1.");
        } catch (error) {
          db.close();
          fail(error);
          return;
        }
        this.db = db;
        const unavailable = (error: unknown) => {
          if (this.db !== db) return;
          this.close();
          for (const listener of this.listeners) listener(error);
        };
        db.onversionchange = () =>
          unavailable(
            new Error(
              "Версия истории изменена другой вкладкой. Перезагрузите страницу.",
            ),
          );
        db.onclose = () =>
          unavailable(new Error("Соединение с историей закрыто браузером."));
        resolve();
      };
    });
    this.opening = attempt;
    void attempt
      .finally(() => {
        if (this.opening === attempt) this.opening = null;
      })
      .catch(() => {});
    return attempt;
  }
  private transaction<T>(
    mode: IDBTransactionMode,
    work: (
      store: IDBObjectStore,
      done: (value: T) => void,
      guard: (operation: () => void) => void,
    ) => void,
  ): Promise<T> {
    return new Promise((resolve, reject) => {
      if (!this.db) {
        reject(new Error("История не открыта."));
        return;
      }
      let result: T, failure: unknown;
      const tx = this.db.transaction(HISTORY_STORE, mode);
      tx.oncomplete = () => resolve(result);
      tx.onabort = () =>
        reject(
          failure ?? tx.error ?? new Error("Транзакция истории отменена."),
        );
      // Let failed requests abort the entire transaction. Never resolve on request success.
      tx.onerror = () => {};
      const guard = (operation: () => void) => {
        try {
          operation();
        } catch (error) {
          failure = error;
          tx.abort();
        }
      };
      guard(() =>
        work(
          tx.objectStore(HISTORY_STORE),
          (value) => {
            result = value;
          },
          guard,
        ),
      );
    });
  }
  private scan(
    store: IDBObjectStore,
    guard: (operation: () => void) => void,
    done: (records: HistoryEntry[]) => void,
  ): void {
    const records: HistoryEntry[] = [];
    const codes = new Set<string>();
    const request = store.openCursor();
    request.onsuccess = () =>
      guard(() => {
        const cursor = request.result;
        if (cursor) {
          const entry = historyEntry(cursor.value);
          if (
            codes.has(entry.code) ||
            records.length >= defaultLimits().history_entries
          )
            throw new Error(
              "Повреждена история: повтор исходника или превышена ёмкость.",
            );
          codes.add(entry.code);
          records.push(entry);
          cursor.continue();
        } else done(records);
      });
  }
  list(): Promise<readonly HistoryEntry[]> {
    return this.transaction("readonly", (store, done, guard) =>
      this.scan(store, guard, (records) =>
        done(Object.freeze(records.sort(compareHistory))),
      ),
    );
  }
  recordAccepted(source: AcceptedSource): Promise<HistoryEntry> {
    if (typeof source.code !== "string" || !validTimestamp(source.acceptedAt))
      return Promise.reject(new Error("Неверные данные принятого запуска."));
    return this.transaction("readwrite", (store, done, guard) =>
      this.scan(store, guard, (records) => {
        const existing = records.find((entry) => entry.code === source.code);
        const entry = historyEntry({
          id: existing?.id ?? this.uuid(),
          code: source.code,
          createdAt: existing?.createdAt ?? source.acceptedAt,
          lastRunAt: source.acceptedAt,
          status: "STARTING",
        });
        store.put(entry);
        const next = [...records.filter((r) => r.id !== entry.id), entry].sort(
          compareHistory,
        );
        for (const old of next.slice(defaultLimits().history_entries))
          store.delete(old.id);
        done(entry);
      }),
    );
  }
  updateStatus(owner: HistoryAssociation, status: RunStatus): Promise<boolean> {
    if (!validStatus(status))
      return Promise.reject(new Error("Неверный статус истории."));
    return this.transaction("readwrite", (store, done, guard) => {
      const request = store.get(owner.historyId);
      request.onsuccess = () =>
        guard(() => {
          const record =
            request.result === undefined
              ? undefined
              : historyEntry(request.result);
          const applies =
            !!record &&
            record.code === owner.code &&
            record.lastRunAt === owner.acceptedAt;
          if (applies) store.put({ ...record, status });
          done(applies);
        });
    });
  }
  delete(id: string): Promise<void> {
    return this.transaction("readwrite", (store, done) => {
      store.delete(id);
      done(undefined);
    });
  }
  markStaleActiveInterrupted(): Promise<void> {
    return this.transaction("readwrite", (store, done, guard) =>
      this.scan(store, guard, (records) => {
        for (const record of records)
          if (ACTIVE_STATUSES.has(record.status))
            store.put({ ...record, status: "INTERRUPTED" });
        done(undefined);
      }),
    );
  }
  close(): void {
    ++this.generation;
    this.db?.close();
    this.db = null;
  }
}
