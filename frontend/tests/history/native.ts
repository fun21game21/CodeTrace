/** Explicit test-only page. Never included in the normal production Vite build. */
import { createElement } from "react";
import { createRoot } from "react-dom/client";
import { App } from "../../src/app/App.tsx";
import { RunController } from "../../src/runs/controller.ts";
import { browserDependencies } from "../../src/runs/browser.ts";
import { ViewerController } from "../../src/viewer/controller.ts";
import { WorkerTraceLoader } from "../../src/execution/worker_session.ts";
import {
  IndexedDbHistoryStore,
  HistoryController,
  bindRunHistory,
  HISTORY_DB,
} from "../../src/history/index.ts";
import type { HistoryEntry } from "../../src/history/types.ts";
import type { AcceptedRun } from "../../src/runs/types.ts";
import "../../src/app/styles.css";
const A = "x = 1\n",
  B = "x = 1 \n",
  INPUT = 'answer = input("Ответ: ")\nprint(answer)\n';
interface Evidence {
  origin: string;
  fresh: boolean;
  checks: Record<string, unknown>;
  first?: HistoryEntry;
  beforeReload?: readonly HistoryEntry[];
  stale?: HistoryEntry;
  beforeOpen?: Record<string, number>;
  expectedOpen?: string;
}
const saved = sessionStorage.getItem("codetrace-history-test-evidence");
const evidence: Evidence = saved
  ? JSON.parse(saved)
  : { origin: location.origin, fresh: false, checks: {} };
const counts = {
  starts: 0,
  sockets: 0,
  traceGets: 0,
  releases: 0,
  workersCreated: 0,
  workersDisposed: 0,
};
const accepted: AcceptedRun[] = [];
function check(value: unknown, message: string): asserts value {
  if (!value) throw Error(message);
}
function renderEvidence() {
  if (safe)
    sessionStorage.setItem(
      "codetrace-history-test-evidence",
      JSON.stringify(evidence),
    );
  document.getElementById("native-evidence")!.textContent = JSON.stringify(
    {
      ...evidence,
      counts,
      runStatus: run.getSnapshot().active.status,
      trace: run.getSnapshot().trace.phase,
      viewer: viewer.getSnapshot().phase,
    },
    null,
    2,
  );
}
const base = browserDependencies();
const loader = new WorkerTraceLoader(() => {
  counts.workersCreated++;
  const worker = new Worker(
    new URL("../../src/execution/trace.worker.ts", import.meta.url),
    { type: "module", name: "codetrace-trace" },
  );
  const post = worker.postMessage.bind(worker);
  worker.postMessage = (message) => {
    if (message.type === "load") counts.traceGets++;
    post(message);
  };
  const terminate = worker.terminate.bind(worker);
  worker.terminate = () => {
    counts.workersDisposed++;
    terminate();
  };
  return worker;
});
const run = new RunController({
  ...base,
  traceLoader: loader,
  socket() {
    counts.sockets++;
    const socket = base.socket();
    return {
      ...socket,
      send(raw) {
        if (JSON.parse(raw).type === "start") counts.starts++;
        socket.send(raw);
      },
    };
  },
  fetch(path, options) {
    if (path.endsWith("/release")) counts.releases++;
    return base.fetch(path, options);
  },
});
const viewer = new ViewerController();
const store = new IndexedDbHistoryStore(),
  history = new HistoryController(store, () => !run.canLoadDraft());
bindRunHistory(run, history);
run.subscribeLifecycle((event) => {
  if (event.type === "accepted") accepted.push(event.run);
});
async function wait(predicate: () => boolean) {
  const deadline = Date.now() + 45000;
  while (!predicate()) {
    check(Date.now() < deadline, "native acceptance deadline");
    await new Promise((r) => setTimeout(r, 10));
  }
}
async function records(): Promise<HistoryEntry[]> {
  const db = await new Promise<IDBDatabase>((resolve, reject) => {
    const r = indexedDB.open(HISTORY_DB, 1);
    r.onsuccess = () => resolve(r.result);
    r.onerror = () => reject(r.error);
  });
  const tx = db.transaction("entries", "readonly"),
    s = tx.objectStore("entries");
  check(s.keyPath === "id" && db.version === 1, "schema version/keyPath");
  check(
    [...s.indexNames].sort().join(",") === "createdAt,lastRunAt",
    "exact indexes",
  );
  check(
    !s.index("createdAt").unique && !s.index("lastRunAt").unique,
    "non-unique indexes",
  );
  const value = await new Promise<HistoryEntry[]>((resolve, reject) => {
    const r = s.getAll();
    r.onsuccess = () => resolve(r.result);
    r.onerror = () => reject(r.error);
  });
  db.close();
  for (const e of value)
    check(
      Object.keys(e).sort().join(",") === "code,createdAt,id,lastRunAt,status",
      "exact five persisted fields",
    );
  return value;
}
async function realReady() {
  await wait(
    () =>
      run.getSnapshot().trace.phase === "READY" &&
      !run.getSnapshot().trace.releasePending &&
      viewer.getSnapshot().committedStep === 1 &&
      history
        .getSnapshot()
        .entries.some(
          (e) =>
            e.code === run.getSnapshot().active.sourceCode &&
            e.status === run.getSnapshot().active.status,
        ),
  );
}
const tools = document.getElementById("test-tools")!;
tools.className = "history-panel";
tools.innerHTML =
  '<h2>HISTORY native IndexedDB · test only · 127.0.0.1:8000</h2><div id="native-actions" class="history-actions"></div><p id="native-result" role="status">Initializing…</p><details><summary>Native evidence</summary><pre id="native-evidence"></pre></details>';
let safe = false;
function button(label: string, action: () => void | Promise<void>) {
  const b = document.createElement("button");
  b.textContent = label;
  b.addEventListener("click", () => {
    void (async () => {
      try {
        check(safe, "Refusing fixture mutation of unowned existing history");
        await action();
        document.getElementById("native-result")!.textContent =
          "PASS · " + label;
        renderEvidence();
      } catch (error) {
        document.getElementById("native-result")!.textContent =
          "FAIL · " + String(error);
      }
    })();
  });
  document.getElementById("native-actions")!.append(b);
}
button("Загрузить A", () => {
  check(run.loadDraft(A), "draft loading disabled");
});
button("Загрузить B", () => {
  check(run.loadDraft(B), "draft loading disabled");
});
button("Загрузить Input", () => {
  check(run.loadDraft(INPUT), "draft loading disabled");
});
button("Первый Run: проверить", async () => {
  await realReady();
  const list = await records();
  check(
    list.length === 1 && list[0]!.code === A && list[0]!.status === "SUCCEEDED",
    "first record",
  );
  check(
    list[0]!.createdAt === accepted[0]!.acceptedAt &&
      list[0]!.lastRunAt === accepted[0]!.acceptedAt,
    "backend acceptedAt",
  );
  check(
    counts.starts === 1 && counts.traceGets === 1 && counts.releases === 1,
    "one first execution",
  );
  evidence.first = list[0]!;
  evidence.checks.firstRun = { record: list[0], counts: { ...counts } };
});
button("Повтор: проверить", async () => {
  await realReady();
  const list = await records(),
    first = evidence.first!;
  check(list.length === 1, "dedupe count");
  const e = list[0]!;
  check(
    e.code === A &&
      e.id === first.id &&
      e.createdAt === first.createdAt &&
      e.lastRunAt !== first.lastRunAt,
    "rerun identity/timestamps",
  );
  check(counts.starts === 2, "two actual executions only");
  evidence.checks.rerun = { record: e, count: 1, counts: { ...counts } };
});
button("Вторая версия: проверить", async () => {
  await realReady();
  const list = await records();
  check(
    list.length === 2 &&
      list.some((e) => e.code === A) &&
      list.some((e) => e.code === B),
    "exact whitespace versions",
  );
  const sorted = history.getSnapshot().entries;
  check(sorted[0]!.code === B, "UI newest first");
  check(
    document
      .querySelectorAll("[data-history-id]")[0]
      ?.getAttribute("data-history-id") === sorted[0]!.id,
    "rendered UI order",
  );
  evidence.checks.secondSource = {
    count: 2,
    order: sorted.map((e) => e.code),
    counts: { ...counts },
  };
});
button("Открытие: подготовить", async () => {
  await realReady();
  viewer.setVisualizerConfig({ kind: "call_stack" });
  await wait(
    () =>
      viewer.getSnapshot().pendingStep === null &&
      viewer.getSnapshot().visualizerConfig.kind === "call_stack",
  );
  evidence.beforeOpen = { ...counts };
  evidence.expectedOpen = A;
});
button("Открытие: проверить", () => {
  const prior = evidence.beforeOpen!;
  check(
    run.getSnapshot().editor.draftCode === evidence.expectedOpen &&
      run.getSnapshot().editor.mode === "editing",
    "exact editable draft",
  );
  check(
    run.getSnapshot().trace.phase === "EMPTY" &&
      viewer.getSnapshot().phase === "EMPTY" &&
      !viewer.getSnapshot().playing &&
      viewer.getSnapshot().visualizerConfig.kind === "none",
    "Worker/Viewer/config cleared",
  );
  for (const name of [
    "starts",
    "sockets",
    "traceGets",
    "releases",
    "workersCreated",
  ] as const)
    check(counts[name] === prior[name], "unexpected open network/Run");
  check(
    counts.workersDisposed === prior.workersDisposed! + 1,
    "one Worker disposed",
  );
  check(
    document.querySelector(".monaco-host")?.getAttribute("data-read-only") ===
      "false",
    "Monaco editable",
  );
  evidence.checks.historyOpen = {
    wsStarts: 0,
    traceGets: 0,
    releases: 0,
    pythonRuns: 0,
    secondWorker: 0,
    workerDisposed: 1,
    viewer: "EMPTY",
    config: "none",
    exactDraft: true,
  };
});
button("Удаление: проверить", async () => {
  await wait(() => history.getSnapshot().entries.length === 1);
  check((await records()).length === 1, "native delete");
  evidence.checks.delete = { count: 1, starts: counts.starts };
});
button("Reload: подготовить", async () => {
  evidence.beforeReload = await records();
  evidence.checks.beforeReload = { count: evidence.beforeReload.length };
});
button("Reload: проверить", async () => {
  await wait(() => history.getSnapshot().phase === "READY");
  const list = await records();
  check(
    JSON.stringify(list) === JSON.stringify(evidence.beforeReload),
    "persistent exact records",
  );
  evidence.checks.reload = {
    sameRecords: true,
    count: list.length,
    starts: counts.starts,
  };
});
button("Input: проверить блокировку", async () => {
  await wait(() => run.getSnapshot().active.status === "WAITING_INPUT");
  check(
    [...document.querySelectorAll<HTMLButtonElement>(".history-actions button")]
      .filter((b) => b.textContent === "Открыть")
      .every((b) => b.disabled),
    "all History Open disabled",
  );
  check(
    run.getSnapshot().active.sourceCode === INPUT,
    "active source unchanged",
  );
  const before = run.getSnapshot().editor.draftCode;
  check(!run.loadDraft(A), "active load no-op");
  check(run.getSnapshot().editor.draftCode === before, "draft unchanged");
  evidence.checks.activeInput = {
    openDisabled: true,
    sourceUnchanged: true,
    status: "WAITING_INPUT",
  };
});
button("Input: проверить итог", async () => {
  await realReady();
  const list = await records();
  check(
    list.find((e) => e.code === INPUT)?.status === "SUCCEEDED",
    "input terminal status",
  );
  check(!list.some((e) => Object.keys(e).length !== 5), "input not persisted");
  evidence.checks.inputTerminal = {
    status: "SUCCEEDED",
    fields: 5,
    counts: { ...counts },
  };
});
button("Stale: создать", async () => {
  check(run.canLoadDraft(), "active Run exists");
  run.loadDraft(A);
  const e = await store.recordAccepted({
    code: "# HISTORY stale fixture\n",
    acceptedAt: "2026-10-07T12:00:00.000001Z",
  });
  await store.updateStatus(
    {
      historyId: e.id,
      runId: crypto.randomUUID(),
      code: e.code,
      acceptedAt: e.lastRunAt,
    },
    "RUNNING",
  );
  evidence.stale = { ...e, status: "RUNNING" };
  evidence.checks.staleSetup = { ...e, status: "RUNNING" };
});
button("Stale: проверить после reload", async () => {
  await wait(() => history.getSnapshot().phase === "READY");
  const stale = evidence.stale!,
    e = (await records()).find((e) => e.id === stale.id)!;
  check(
    e.status === "INTERRUPTED" &&
      e.createdAt === stale.createdAt &&
      e.lastRunAt === stale.lastRunAt,
    "stale recovery only status",
  );
  evidence.checks.staleReload = {
    status: e.status,
    timestampsUnchanged: true,
    starts: counts.starts,
  };
});
button("Native races и capacity", async () => {
  check(run.canLoadDraft(), "active Run exists");
  const before = await records();
  for (const e of before) await store.delete(e.id);
  try {
    const t1 = "2026-10-07T00:00:00.000001Z",
      t2 = "2026-10-07T00:00:00.000002Z";
    const old = await store.recordAccepted({ code: "X", acceptedAt: t1 }),
      recent = await store.recordAccepted({ code: "X", acceptedAt: t2 });
    check(
      !(await store.updateStatus(
        {
          historyId: old.id,
          runId: crypto.randomUUID(),
          code: old.code,
          acceptedAt: t1,
        },
        "FAILED",
      )),
      "late terminal native guard",
    );
    await store.updateStatus(
      {
        historyId: recent.id,
        runId: crypto.randomUUID(),
        code: recent.code,
        acceptedAt: t2,
      },
      "SUCCEEDED",
    );
    await Promise.all(
      Array.from({ length: 12 }, () =>
        store.recordAccepted({ code: "X", acceptedAt: t2 }),
      ),
    );
    check((await store.list()).length === 1, "native concurrent dedupe");
    for (const code of [A, B, "s='é'\n", "s='e\u0301'\n", "x=1\r\n"])
      await store.recordAccepted({ code, acceptedAt: t2 });
    check(
      (await store.list()).length === 6,
      "native exact unicode/whitespace/CRLF",
    );
    for (const e of await store.list()) await store.delete(e.id);
    const inserted: HistoryEntry[] = [];
    for (let i = 0; i < 100; i++)
      inserted.push(
        await store.recordAccepted({
          code: "# capacity " + i,
          acceptedAt: new Date(Date.UTC(2026, 9, 7, 0, 0, i)).toISOString(),
        }),
      );
    check((await records()).length === 100, "native capacity 100");
    const newest = await store.recordAccepted({
        code: "# capacity 100",
        acceptedAt: "2026-10-08T00:00:00Z",
      }),
      list = await store.list();
    check(
      list.length === 100 &&
        list[0]!.id === newest.id &&
        !list.some((e) => e.id === inserted[0]!.id),
      "native 101 eviction",
    );
    await Promise.all(
      Array.from({ length: 10 }, (_, i) =>
        store.recordAccepted({
          code: "# concurrent " + i,
          acceptedAt: "2026-10-09T00:00:00Z",
        }),
      ),
    );
    check((await records()).length === 100, "native concurrent capacity");
    evidence.checks.nativeStore = {
      sameCodeConcurrent: true,
      lateOldTerminalNoop: true,
      exactUnicode: true,
      exactWhitespace: true,
      exactCRLF: true,
      capacity100: 100,
      after101: 100,
      oldestEvicted: true,
      concurrentCapacity: 100,
    };
  } finally {
    for (const e of await store.list()) await store.delete(e.id);
    // Restore only owned test records, preserving the exact five-field snapshots.
    const request = indexedDB.open(HISTORY_DB, 1);
    const db = await new Promise<IDBDatabase>((r) => {
      request.onsuccess = () => r(request.result);
    });
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction("entries", "readwrite");
      for (const e of before) tx.objectStore("entries").put(e);
      tx.oncomplete = () => resolve();
      tx.onabort = () => reject(tx.error);
    });
    db.close();
    await history.initialize();
  }
});
button("Exact CRLF: подготовить", async () => {
  check(run.canLoadDraft(), "active Run exists");
  const code = "# <script>\r\ns='e\u0301'\r\n";
  await store.recordAccepted({ code, acceptedAt: "2026-10-07T13:00:00Z" });
  await history.initialize();
  evidence.expectedOpen = code;
  evidence.beforeOpen = { ...counts };
});
button("Exact CRLF: проверить", () => {
  check(
    run.getSnapshot().editor.draftCode === evidence.expectedOpen,
    "Monaco callback normalized exact draft",
  );
  for (const name of ["starts", "sockets", "traceGets", "releases"] as const)
    check(
      counts[name] === evidence.beforeOpen![name],
      "exact-source open started network",
    );
  evidence.checks.exactDraft = {
    CRLF: true,
    unicode: true,
    HTMLPlainText: true,
    exact: true,
    noExecution: true,
  };
});
button("Inspect native records", async () => {
  evidence.checks.inspection = await records();
});
const databases = await indexedDB.databases();
if (!saved) {
  evidence.fresh = !databases.some((db) => db.name === HISTORY_DB);
  if (!evidence.fresh) {
    await store.open();
    safe = (await store.list()).length === 0;
  } else safe = true;
} else safe = true;
check(
  location.origin === "http://127.0.0.1:8000",
  "production origin required",
);
await history.initialize();
createRoot(document.getElementById("root")!).render(
  createElement(App, { controller: run, viewer, history }),
);
renderEvidence();
document.getElementById("native-result")!.textContent = safe
  ? "READY · native IndexedDB acceptance"
  : "UNOWNED EXISTING HISTORY · controlled writes refused";
window.addEventListener(
  "pagehide",
  () => {
    run.dispose();
    history.dispose();
    viewer.dispose();
  },
  { once: true },
);
