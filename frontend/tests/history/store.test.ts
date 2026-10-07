import assert from "node:assert/strict";
import test from "node:test";
import { IDBFactory } from "fake-indexeddb";
import {
  IndexedDbHistoryStore,
  HISTORY_DB,
} from "../../src/history/indexeddb.ts";
import {
  historyEntry,
  compareHistory,
  validTimestamp,
} from "../../src/history/values.ts";
import type { RunStatus } from "../../../contracts/typescript/index.ts";
import { fresh, raw, putRaw, owner, T1, T2 } from "./helpers.ts";
const fields = ["code", "createdAt", "id", "lastRunAt", "status"];
for (const property of [
  "name",
  "version",
  "stores",
  "keyPath",
  "lastRunAt",
  "createdAt",
  "no-code-index",
])
  test("native schema contract: " + property, async () => {
    const h = await fresh(),
      db = await raw(h.factory),
      s = db.transaction("entries").objectStore("entries");
    if (property === "name") assert.equal(db.name, "codetrace-history");
    else if (property === "version") assert.equal(db.version, 1);
    else if (property === "stores")
      assert.deepEqual([...db.objectStoreNames], ["entries"]);
    else if (property === "keyPath") assert.equal(s.keyPath, "id");
    else if (property === "no-code-index")
      assert.deepEqual([...s.indexNames], ["createdAt", "lastRunAt"]);
    else {
      const i = s.index(property);
      assert.equal(i.keyPath, property);
      assert(!i.unique && !i.multiEntry);
    }
    db.close();
    h.store.close();
  });
test("accept commit is one transaction, UUID, exact five fields, backend timestamps", async () => {
  const h = await fresh(),
    entry = await h.store.recordAccepted({ code: "x = 1\n", acceptedAt: T1 });
  assert.deepEqual(h.modes, ["readwrite"]);
  assert.match(entry.id, /^[\da-f-]{36}$/);
  assert.equal(entry.createdAt, T1);
  assert.equal(entry.lastRunAt, T1);
  assert.equal(entry.status, "STARTING");
  const db = await raw(h.factory);
  const request = db
    .transaction("entries")
    .objectStore("entries")
    .get(entry.id);
  const persisted = await new Promise<unknown>((resolve) => {
    request.onsuccess = () => resolve(request.result);
  });
  assert.deepEqual(Object.keys(persisted as object).sort(), fields);
  assert.deepEqual(persisted, entry);
  db.close();
  h.store.close();
});
test("exact rerun preserves id and createdAt and moves to top without growing", async () => {
  const h = await fresh(),
    a = await h.store.recordAccepted({ code: "A", acceptedAt: T1 });
  await h.store.updateStatus(owner(a), "SUCCEEDED");
  await h.store.recordAccepted({ code: "B", acceptedAt: T2 });
  const b = await h.store.recordAccepted({
    code: "A",
    acceptedAt: "2026-10-07T11:00:00Z",
  });
  assert.equal(a.id, b.id);
  assert.equal(b.createdAt, T1);
  assert.equal(b.status, "STARTING");
  assert.notEqual(b.lastRunAt, a.lastRunAt);
  const list = await h.store.list();
  assert.equal(list.length, 2);
  assert.equal(list[0]!.id, a.id);
  h.store.close();
});
for (const [name, a, b] of [
  ["trailing-space", "x = 1\n", "x = 1 \n"],
  ["leading-space", "x=1", " x=1"],
  ["comment", "x=1", "x=1 # comment"],
  ["CRLF", "x=1\n", "x=1\r\n"],
  ["unicode", "s='é'", "s='e\u0301'"],
  ["case", "x=1", "X=1"],
  ["empty", "", "\n"],
  ["surrogate", "s='\ud800'", "s='\ufffd'"],
] as const)
  test("exact source identity: " + name, async () => {
    const h = await fresh(),
      first = await h.store.recordAccepted({ code: a, acceptedAt: T1 }),
      second = await h.store.recordAccepted({ code: b, acceptedAt: T2 });
    assert.notEqual(first.id, second.id);
    assert.deepEqual(
      (await h.store.list()).map((e) => e.code),
      [b, a],
    );
    h.store.close();
  });
test("64KiB source is persisted whole; only previews are bounded", async () => {
  const h = await fresh(),
    code = "#".repeat(65536),
    entry = await h.store.recordAccepted({ code, acceptedAt: T1 });
  assert.equal(entry.code, code);
  assert.equal((await h.store.list())[0]!.code, code);
  h.store.close();
});
for (const status of [
  "STARTING",
  "RUNNING",
  "WAITING_INPUT",
  "FINALIZING",
  "SUCCEEDED",
  "FAILED",
  "LIMIT_EXCEEDED",
  "INTERRUPTED",
] as const)
  test(
    "guarded status " + status + " preserves ownership/timestamps",
    async () => {
      const h = await fresh(),
        e = await h.store.recordAccepted({ code: "X", acceptedAt: T1 });
      assert(await h.store.updateStatus(owner(e), status));
      assert.deepEqual((await h.store.list())[0], { ...e, status });
      h.store.close();
    },
  );
test("WAITING_INPUT resumes RUNNING with the same history source", async () => {
  const h = await fresh(),
    e = await h.store.recordAccepted({ code: "input()", acceptedAt: T1 }),
    o = owner(e);
  for (const status of ["RUNNING", "WAITING_INPUT", "RUNNING"] as const)
    await h.store.updateStatus(o, status);
  assert.equal((await h.store.list())[0]!.status, "RUNNING");
  h.store.close();
});
for (const change of ["wrong-code", "wrong-timestamp", "missing"])
  test("stale status no-op: " + change, async () => {
    const h = await fresh(),
      e = await h.store.recordAccepted({ code: "X", acceptedAt: T1 }),
      o = owner(e);
    const wrong =
      change === "wrong-code"
        ? { ...o, code: "X " }
        : change === "wrong-timestamp"
          ? { ...o, acceptedAt: T2 }
          : { ...o, historyId: crypto.randomUUID() };
    assert.equal(await h.store.updateStatus(wrong, "FAILED"), false);
    assert.deepEqual((await h.store.list())[0], e);
    h.store.close();
  });
test("critical old terminal cannot overwrite newer same-source Run", async () => {
  const h = await fresh(),
    a = await h.store.recordAccepted({ code: "X", acceptedAt: T1 }),
    b = await h.store.recordAccepted({ code: "X", acceptedAt: T2 });
  await h.store.updateStatus(owner(b), "RUNNING");
  assert.equal(await h.store.updateStatus(owner(a), "FAILED"), false);
  assert.equal((await h.store.list())[0]!.status, "RUNNING");
  await h.store.updateStatus(owner(b), "SUCCEEDED");
  assert.deepEqual((await h.store.list())[0], { ...b, status: "SUCCEEDED" });
  h.store.close();
});
for (const status of [
  "STARTING",
  "RUNNING",
  "WAITING_INPUT",
  "FINALIZING",
  "SUCCEEDED",
  "FAILED",
  "LIMIT_EXCEEDED",
  "INTERRUPTED",
] as const)
  test("startup recovery: " + status, async () => {
    const h = await fresh(),
      e = await h.store.recordAccepted({ code: "X", acceptedAt: T1 });
    await h.store.updateStatus(owner(e), status);
    h.modes.length = 0;
    await h.store.markStaleActiveInterrupted();
    assert.deepEqual(h.modes, ["readwrite"]);
    const expected = [
      "STARTING",
      "RUNNING",
      "WAITING_INPUT",
      "FINALIZING",
    ].includes(status)
      ? "INTERRUPTED"
      : status;
    assert.deepEqual((await h.store.list())[0], { ...e, status: expected });
    h.store.close();
  });
test("100 → 101 eviction, retained rerun promotion and evicted source new UUID", async () => {
  const h = await fresh();
  const entries: import("../../src/history/types.ts").HistoryEntry[] = [];
  for (let i = 0; i < 100; i++)
    entries.push(
      await h.store.recordAccepted({
        code: "x=" + i,
        acceptedAt: new Date(Date.UTC(2026, 9, 7, 0, 0, i)).toISOString(),
      }),
    );
  assert.equal((await h.store.list()).length, 100);
  h.modes.length = 0;
  const newest = await h.store.recordAccepted({
    code: "x=100",
    acceptedAt: "2026-10-08T00:00:00Z",
  });
  assert.deepEqual(h.modes, ["readwrite"]);
  let list = await h.store.list();
  assert.equal(list.length, 100);
  assert.equal(list[0]!.id, newest.id);
  assert(!list.some((e) => e.id === entries[0]!.id));
  const rerun = await h.store.recordAccepted({
    code: entries[1]!.code,
    acceptedAt: "2026-10-09T00:00:00Z",
  });
  list = await h.store.list();
  assert.equal(list.length, 100);
  assert.equal(list[0]!.id, entries[1]!.id);
  assert.equal(rerun.createdAt, entries[1]!.createdAt);
  const restored = await h.store.recordAccepted({
    code: entries[0]!.code,
    acceptedAt: "2026-10-10T00:00:00Z",
  });
  assert.notEqual(restored.id, entries[0]!.id);
  assert.equal((await h.store.list()).length, 100);
  h.store.close();
});
test("tie eviction uses the same UUID ascending order as UI", async () => {
  const factory = new IDBFactory();
  let id = 0;
  const store = new IndexedDbHistoryStore(
    factory,
    () => "00000000-0000-4000-8000-" + (++id).toString(16).padStart(12, "0"),
  );
  await store.open();
  for (let i = 0; i < 101; i++)
    await store.recordAccepted({ code: "x=" + i, acceptedAt: T1 });
  const list = await store.list();
  assert.equal(list.length, 100);
  assert.equal(list[0]!.code, "x=0");
  assert(!list.some((e) => e.code === "x=100"));
  store.close();
});
for (const same of [true, false])
  test(
    "concurrent accepts " + (same ? "same" : "different") + " exact sources",
    async () => {
      const h = await fresh();
      await Promise.all(
        Array.from({ length: 20 }, (_, i) =>
          h.store.recordAccepted({
            code: same ? "X" : "X" + i,
            acceptedAt: i % 2 ? T2 : T1,
          }),
        ),
      );
      const list = await h.store.list();
      assert.equal(list.length, same ? 1 : 20);
      assert.equal(new Set(list.map((e) => e.code)).size, list.length);
      h.store.close();
    },
  );
test("concurrent capacity insertions commit at <=100 with exact eviction", async () => {
  const h = await fresh();
  await Promise.all(
    Array.from({ length: 110 }, (_, i) =>
      h.store.recordAccepted({
        code: "x=" + i,
        acceptedAt: new Date(Date.UTC(2026, 9, 7, 0, 0, i)).toISOString(),
      }),
    ),
  );
  const list = await h.store.list();
  assert.equal(list.length, 100);
  assert.equal(list[0]!.code, "x=109");
  assert.equal(list[99]!.code, "x=10");
  h.store.close();
});
for (const terminalFirst of [true, false])
  test("rerun / old terminal transaction race " + terminalFirst, async () => {
    const h = await fresh(),
      e = await h.store.recordAccepted({ code: "X", acceptedAt: T1 });
    if (terminalFirst)
      await Promise.all([
        h.store.updateStatus(owner(e), "FAILED"),
        h.store.recordAccepted({ code: "X", acceptedAt: T2 }),
      ]);
    else
      await Promise.all([
        h.store.recordAccepted({ code: "X", acceptedAt: T2 }),
        h.store.updateStatus(owner(e), "FAILED"),
      ]);
    const list = await h.store.list();
    assert.equal(list.length, 1);
    assert.equal(list[0]!.lastRunAt, T2);
    assert.equal(list[0]!.status, "STARTING");
    h.store.close();
  });
for (const deleteFirst of [true, false])
  test("delete / status transaction race " + deleteFirst, async () => {
    const h = await fresh(),
      e = await h.store.recordAccepted({ code: "X", acceptedAt: T1 });
    if (deleteFirst)
      await Promise.all([
        h.store.delete(e.id),
        h.store.updateStatus(owner(e), "SUCCEEDED"),
      ]);
    else
      await Promise.all([
        h.store.updateStatus(owner(e), "SUCCEEDED"),
        h.store.delete(e.id),
      ]);
    assert.equal((await h.store.list()).length, 0);
    h.store.close();
  });
for (const fault of ["abort", "quota", "put"] as const)
  test("failed acceptance rolls back: " + fault, async () => {
    const h = await fresh();
    h.inject(fault);
    await assert.rejects(h.store.recordAccepted({ code: "X", acceptedAt: T1 }));
    assert.deepEqual(await h.store.list(), []);
    h.store.close();
  });
test("delete failure retains the record and reports rejection", async () => {
  const h = await fresh(),
    e = await h.store.recordAccepted({ code: "X", acceptedAt: T1 });
  h.inject("delete");
  await assert.rejects(h.store.delete(e.id));
  assert.deepEqual((await h.store.list())[0], e);
  h.store.close();
});
test("capacity delete failure rolls back the preceding put", async () => {
  const h = await fresh();
  for (let i = 0; i < 100; i++)
    await h.store.recordAccepted({ code: "X" + i, acceptedAt: T1 });
  const before = await h.store.list();
  h.inject("delete");
  await assert.rejects(h.store.recordAccepted({ code: "NEW", acceptedAt: T2 }));
  assert.deepEqual(await h.store.list(), before);
  h.store.close();
});
for (const bad of [
  null,
  [],
  { id: "x" },
  {
    id: crypto.randomUUID(),
    code: "X",
    createdAt: T1,
    lastRunAt: T1,
    status: "IDLE",
  },
  {
    id: crypto.randomUUID(),
    code: "X",
    createdAt: T1,
    lastRunAt: T1,
    status: "RUNNING",
    trace: {},
  },
])
  test(
    "untrusted entry validator rejects invalid shape " + JSON.stringify(bad),
    () => assert.throws(() => historyEntry(bad)),
  );
for (const timestamp of [
  "2026-02-30T00:00:00Z",
  "2026-10-07T24:00:00Z",
  "2026-10-07T00:00:00+00:00",
  "2026-10-07",
  "",
])
  test("reject non-UTC or invalid calendar timestamp " + timestamp, () =>
    assert.equal(validTimestamp(timestamp), false),
  );
test("corrupt persisted entry rejects list and recovery without repairing source", async () => {
  const h = await fresh(),
    e = await h.store.recordAccepted({ code: "X", acceptedAt: T1 });
  await putRaw(h.factory, { ...e, trace: {} });
  await assert.rejects(h.store.list(), /Повреждена/);
  await assert.rejects(h.store.markStaleActiveInterrupted());
  h.store.close();
});
test("microsecond comparator and tie createdAt order are chronological", () => {
  const base = {
    id: crypto.randomUUID(),
    code: "X",
    createdAt: T1,
    lastRunAt: T1,
    status: "STARTING" as RunStatus,
  };
  assert(compareHistory(base, { ...base, lastRunAt: T2 }) > 0);
  assert(
    compareHistory({ ...base, lastRunAt: "2026-10-07T10:00:00Z" }, base) > 0,
  );
  assert(compareHistory(base, { ...base, createdAt: T2 }) > 0);
});
test("unavailable IndexedDB rejects open without creating storage", async () => {
  await assert.rejects(
    new IndexedDbHistoryStore(undefined).open(),
    /недоступна/,
  );
});
test("open throwing is a rejected promise", async () => {
  const factory = new IDBFactory();
  factory.open = () => {
    throw new DOMException("denied", "SecurityError");
  };
  await assert.rejects(new IndexedDbHistoryStore(factory).open(), /denied/);
});
test("blocked open rejects promptly and closes a late success", async () => {
  let closed = 0;
  const request = {} as IDBOpenDBRequest;
  const factory = { open: () => request } as unknown as IDBFactory;
  const store = new IndexedDbHistoryStore(factory),
    promise = store.open();
  request.onblocked!(new Event("blocked") as IDBVersionChangeEvent);
  await assert.rejects(promise, /заблокировано/);
  Object.defineProperty(request, "result", {
    value: {
      close() {
        closed++;
      },
    },
  });
  request.onsuccess!(new Event("success"));
  assert.equal(closed, 1);
  store.close();
});
test("versionchange closes connection and lets another version proceed", async () => {
  const h = await fresh();
  let warning: unknown;
  h.store.onUnavailable((error) => {
    warning = error;
  });
  const second = await raw(h.factory, 2);
  assert(warning instanceof Error);
  await assert.rejects(h.store.list(), /не открыта/);
  second.close();
  h.store.close();
});
test("reopen existing v1 preserves all source and timestamps", async () => {
  const h = await fresh(),
    e = await h.store.recordAccepted({ code: "  x=1\r\n", acceptedAt: T1 });
  h.store.close();
  await h.store.open();
  assert.deepEqual((await h.store.list())[0], e);
  h.store.close();
});
test("incompatible existing schema is rejected rather than deleted", async () => {
  const factory = new IDBFactory();
  await new Promise<void>((resolve) => {
    const r = factory.open(HISTORY_DB, 1);
    r.onupgradeneeded = () => r.result.createObjectStore("wrong");
    r.onsuccess = () => {
      r.result.close();
      resolve();
    };
  });
  await assert.rejects(new IndexedDbHistoryStore(factory).open());
  const db = await raw(factory);
  assert(db.objectStoreNames.contains("wrong"));
  db.close();
});
test("invalid accepted timestamp or runtime status rejects without mutation", async () => {
  const h = await fresh();
  await assert.rejects(
    h.store.recordAccepted({ code: "X", acceptedAt: "invalid" }),
  );
  const e = await h.store.recordAccepted({ code: "X", acceptedAt: T1 });
  await assert.rejects(h.store.updateStatus(owner(e), "IDLE" as RunStatus));
  assert.deepEqual((await h.store.list())[0], e);
  h.store.close();
});
test("duplicate persisted exact sources are corruption, never silently deduped", async () => {
  const h = await fresh(),
    e = await h.store.recordAccepted({ code: "X", acceptedAt: T1 });
  await putRaw(h.factory, { ...e, id: crypto.randomUUID() });
  await assert.rejects(h.store.list(), /повтор/);
  await assert.rejects(h.store.recordAccepted({ code: "X", acceptedAt: T2 }));
  h.store.close();
});
test("closed store refuses writes until explicit reopen", async () => {
  const h = await fresh();
  h.store.close();
  await assert.rejects(
    h.store.recordAccepted({ code: "X", acceptedAt: T1 }),
    /не открыта/,
  );
  await h.store.open();
  assert.deepEqual(await h.store.list(), []);
  h.store.close();
});
