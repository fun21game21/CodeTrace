import assert from "node:assert/strict";
import test from "node:test";
import {
  HistoryController,
  bindRunHistory,
  openHistoryEntry,
} from "../../src/history/index.ts";
import { IndexedDbHistoryStore } from "../../src/history/indexeddb.ts";
import { RunController } from "../../src/runs/controller.ts";
import { ViewerController } from "../../src/viewer/controller.ts";
import { bindRunViewer } from "../../src/viewer/binding.ts";
import { TraceCore } from "../../src/trace/index.ts";
import type { LoadedTraceSession } from "../../src/execution/types.ts";
import {
  setup,
  Socket,
  REQUEST,
  RUN,
  health,
  validTrace,
  finish,
} from "../runs/helpers.ts";
import {
  DelayedStore,
  drain,
  until,
  fresh,
  owner,
  putRaw,
  T1,
  T2,
} from "./helpers.ts";
const accepted = (sourceCode = "X", acceptedAt = T1, runId = RUN) => ({
  sourceCode,
  acceptedAt,
  runId,
});
async function controlled() {
  const store = new DelayedStore(),
    history = new HistoryController(store);
  await history.initialize();
  return { store, history };
}
test("controller starts LOADING, opens to READY with frozen sorted state", async () => {
  const store = new DelayedStore(),
    history = new HistoryController(store);
  assert.equal(history.getSnapshot().phase, "LOADING");
  await history.initialize();
  assert.equal(history.getSnapshot().phase, "READY");
  assert(Object.isFrozen(history.getSnapshot()));
  assert(Object.isFrozen(history.getSnapshot().entries));
  history.dispose();
});
test("typing, click, socket_ready and rejection create no history", async () => {
  const h = setup("X"),
    { store, history } = await controlled(),
    unbind = bindRunHistory(h.controller, history);
  await h.controller.refreshHealth();
  h.controller.setDraft("NEW");
  assert.equal(store.accepts.length, 0);
  assert(h.controller.run());
  assert.equal(store.accepts.length, 0);
  const socket = h.sockets[0]!;
  socket.ready();
  assert.equal(store.accepts.length, 0);
  socket.emit(
    "run_rejected",
    { clientRequestId: REQUEST, code: "RUN_BUSY", message: "Busy" },
    null,
  );
  assert.equal(store.accepts.length, 0);
  unbind();
  h.controller.dispose();
  history.dispose();
});
test("accepted lifecycle exposes original source and backend acceptedAt", async () => {
  const h = setup("  s='é'\r\n"),
    { store, history } = await controlled(),
    unbind = bindRunHistory(h.controller, history);
  await h.controller.refreshHealth();
  assert(h.controller.run());
  h.controller.setDraft("LATE EDIT");
  h.sockets[0]!.ready();
  h.sockets[0]!.accept();
  assert.deepEqual(store.accepts, [
    { code: "  s='é'\r\n", acceptedAt: "2026-10-05T20:00:00Z" },
  ]);
  store.finish();
  await drain();
  assert.equal(history.getSnapshot().entries[0]!.code, "  s='é'\r\n");
  unbind();
  h.controller.dispose();
  history.dispose();
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
  test("pending initial upsert buffers latest " + status, async () => {
    const { store, history } = await controlled();
    history.accepted(accepted());
    history.status(RUN, "RUNNING");
    history.status(RUN, "WAITING_INPUT");
    history.status(RUN, status);
    assert.equal(store.updates.length, 0);
    store.finish();
    await drain();
    assert.deepEqual(
      store.updates.map((u) => u.status),
      [status],
    );
    assert.equal(history.getSnapshot().entries[0]!.status, status);
    history.dispose();
  });
test("two accepted writes pending retain their own associations", async () => {
  const { store, history } = await controlled(),
    second = crypto.randomUUID();
  history.accepted(accepted("A"));
  history.accepted(accepted("B", T2, second));
  history.status(RUN, "FAILED");
  history.status(second, "SUCCEEDED");
  store.finish(1);
  store.finish(0);
  await drain();
  assert.equal(
    store.updates.find((u) => u.owner.runId === RUN)!.owner.code,
    "A",
  );
  assert.equal(
    store.updates.find((u) => u.owner.runId === second)!.status,
    "SUCCEEDED",
  );
  assert.equal(history.getSnapshot().entries.length, 2);
  history.dispose();
});
test("late old pending terminal uses original guard after same-code rerun", async () => {
  const h = await fresh(),
    history = new HistoryController(h.store);
  await history.initialize();
  const second = crypto.randomUUID();
  history.accepted(accepted());
  await until(() => history.getSnapshot().entries.length === 1);
  history.accepted(accepted("X", T2, second));
  history.status(second, "RUNNING");
  await until(
    () =>
      history.getSnapshot().entries[0]!.lastRunAt === T2 &&
      history.getSnapshot().entries[0]!.status === "RUNNING",
  );
  history.status(RUN, "FAILED");
  await drain();
  assert.equal((await h.store.list())[0]!.status, "RUNNING");
  history.status(second, "SUCCEEDED");
  await until(() => history.getSnapshot().entries[0]!.status === "SUCCEEDED");
  history.dispose();
});
test("multiple statuses during a pending update serialize and newest wins", async () => {
  const { store, history } = await controlled();
  let resolve: () => void = () => {};
  const actual = store.updateStatus.bind(store);
  store.updateStatus = async (o, s) => {
    if (s === "RUNNING")
      await new Promise<void>((r) => {
        resolve = r;
      });
    return actual(o, s);
  };
  history.accepted(accepted());
  store.finish();
  await drain();
  history.status(RUN, "RUNNING");
  history.status(RUN, "WAITING_INPUT");
  history.status(RUN, "FINALIZING");
  history.status(RUN, "SUCCEEDED");
  resolve();
  await drain();
  assert.deepEqual(
    store.updates.map((u) => u.status),
    ["STARTING", "RUNNING", "SUCCEEDED"],
  );
  assert.equal(history.getSnapshot().entries[0]!.status, "SUCCEEDED");
  history.dispose();
});
test("reset while accepted write pending still commits latest terminal with original owner", async () => {
  const { store, history } = await controlled();
  history.accepted(accepted());
  history.status(RUN, "SUCCEEDED");
  history.reset();
  store.finish();
  await drain();
  assert.equal(history.getSnapshot().entries[0]!.status, "SUCCEEDED");
  history.dispose();
});
test("duplicate acceptance callback is ignored", async () => {
  const { store, history } = await controlled();
  history.accepted(accepted());
  history.accepted(accepted());
  assert.equal(store.accepts.length, 1);
  store.finish();
  await drain();
  history.dispose();
});
test("runtime initialize never recovers a known live Run", async () => {
  const h = await fresh(),
    e = await h.store.recordAccepted({ code: "X", acceptedAt: T1 });
  await h.store.updateStatus(owner(e), "RUNNING");
  const history = new HistoryController(h.store, () => true);
  await history.initialize();
  assert.equal(history.getSnapshot().entries[0]!.status, "RUNNING");
  await history.initialize();
  assert.equal(history.getSnapshot().entries[0]!.status, "RUNNING");
  history.dispose();
});
test("connection that appears during asynchronous open is rechecked", async () => {
  const store = new DelayedStore();
  let resolve: () => void = () => {},
    active = false;
  store.open = () =>
    new Promise<void>((r) => {
      resolve = r;
    });
  const history = new HistoryController(store, () => active),
    pending = history.initialize();
  active = true;
  resolve();
  await pending;
  assert.equal(store.recoveries, 0);
  history.dispose();
});
test("startup recovery turns stale status interrupted and retains timestamps", async () => {
  const h = await fresh(),
    e = await h.store.recordAccepted({ code: "X", acceptedAt: T1 });
  const history = new HistoryController(h.store);
  await history.initialize();
  assert.deepEqual(history.getSnapshot().entries[0], {
    ...e,
    status: "INTERRUPTED",
  });
  history.dispose();
});
for (const fault of ["open", "accepted", "terminal", "delete"] as const)
  test(
    "history " +
      fault +
      " failure never disables Run or changes its terminal result",
    async () => {
      const h = setup("X"),
        store = new DelayedStore();
      store.failOpen = fault === "open";
      store.failAccept = fault === "accepted";
      const history = new HistoryController(store),
        unbind = bindRunHistory(h.controller, history);
      await history.initialize();
      await h.controller.refreshHealth();
      assert(h.controller.canRun());
      assert(h.controller.run());
      const socket = h.sockets[0]!;
      socket.ready();
      socket.accept();
      if (fault !== "open" && fault !== "accepted") {
        store.finish();
        await drain();
      }
      store.failStatus = fault === "terminal";
      finish(socket, "X");
      await drain();
      if (fault === "delete") {
        store.failDelete = true;
        const before = history.getSnapshot().entries;
        assert.equal(await history.delete(before[0]!.id), false);
        assert.deepEqual(history.getSnapshot().entries, before);
      }
      assert.equal(h.controller.getSnapshot().active.status, "SUCCEEDED");
      assert.equal(h.controller.getSnapshot().transportError, null);
      assert(history.getSnapshot().warning);
      h.controller.edit();
      assert(h.controller.canRun());
      if (fault === "accepted") {
        history.status(RUN, "RUNNING");
        await drain();
        assert.equal(store.accepts.length, 1);
        assert.equal(store.updates.length, 0);
      }
      unbind();
      h.controller.dispose();
      history.dispose();
    },
  );
test("missing IndexedDB produces UNAVAILABLE warning independently of health", async () => {
  const history = new HistoryController(new IndexedDbHistoryStore(undefined)),
    h = setup();
  await history.initialize();
  await h.controller.refreshHealth();
  assert.equal(history.getSnapshot().phase, "UNAVAILABLE");
  assert(history.getSnapshot().warning);
  assert(h.controller.canRun());
  h.controller.dispose();
  history.dispose();
});
test("corruption is unavailable without mutating existing arbitrary record", async () => {
  const h = await fresh(),
    e = await h.store.recordAccepted({ code: "X", acceptedAt: T1 });
  await putRaw(h.factory, { ...e, status: "IDLE" });
  const history = new HistoryController(h.store);
  await history.initialize();
  assert.equal(history.getSnapshot().phase, "UNAVAILABLE");
  assert(history.getSnapshot().warning?.includes("Повреждена"));
  history.dispose();
});
test("controller deletion refreshes list; later active status safely no-ops", async () => {
  const h = await fresh(),
    history = new HistoryController(h.store);
  await history.initialize();
  history.accepted(accepted());
  await until(() => history.getSnapshot().entries.length === 1);
  const id = history.getSnapshot().entries[0]!.id;
  assert(await history.delete(id));
  history.status(RUN, "SUCCEEDED");
  await drain();
  assert.deepEqual(history.getSnapshot().entries, []);
  history.dispose();
});
test("history open copies exact full source without creating a socket or HTTP request", async () => {
  const h = setup(),
    { store, history } = await controlled(),
    code = "  # <script>\r\ns='e\u0301'\r\n";
  history.accepted(accepted(code));
  store.finish();
  await drain();
  await h.controller.refreshHealth();
  const before = [h.calls.length, h.sockets.length],
    id = history.getSnapshot().entries[0]!.id;
  assert(openHistoryEntry(h.controller, history, id));
  assert.equal(h.controller.getSnapshot().editor.draftCode, code);
  assert.equal(h.controller.getSnapshot().editor.mode, "editing");
  assert.deepEqual([h.calls.length, h.sockets.length], before);
  h.controller.dispose();
  history.dispose();
});
for (const status of [
  "STARTING",
  "RUNNING",
  "WAITING_INPUT",
  "FINALIZING",
] as const)
  test("history open is no-op during active " + status, async () => {
    const h = setup("ACTIVE"),
      { store, history } = await controlled();
    history.accepted(accepted("OTHER"));
    store.finish();
    await drain();
    await h.controller.refreshHealth();
    h.controller.run();
    const socket = h.sockets[0]!;
    socket.ready();
    socket.accept();
    if (status !== "STARTING") socket.status("RUNNING");
    if (status === "WAITING_INPUT") socket.status(status);
    if (status === "FINALIZING") socket.status(status);
    const before = h.controller.getSnapshot();
    assert.equal(
      openHistoryEntry(
        h.controller,
        history,
        history.getSnapshot().entries[0]!.id,
      ),
      false,
    );
    assert.equal(h.controller.getSnapshot(), before);
    h.controller.dispose();
    history.dispose();
  });
test("connecting socket disables draft loading before acceptance", async () => {
  const h = setup("X");
  await h.controller.refreshHealth();
  h.controller.run();
  assert.equal(h.controller.loadDraft("OTHER"), false);
  h.controller.dispose();
});
test("READY history open detaches Viewer, disposes owned Worker session and resets VISUALIZERS config", async () => {
  const socket = new Socket();
  let workerCreates = 0,
    workerDisposed = 0,
    session: LoadedTraceSession | null = null;
  const requests: string[] = [];
  const core = TraceCore.load(validTrace("ACTIVE"));
  const run = new RunController(
    {
      uuid: () => REQUEST,
      socket: () => socket,
      fetch: async (path) => {
        requests.push(path);
        return {
          status: path.endsWith("release") ? 204 : 200,
          json: async () => health(),
        };
      },
      traceLoader: {
        async load() {
          workerCreates++;
          session = {
            identity: "test-worker",
            runId: RUN,
            sourceCode: "ACTIVE",
            totalSteps: core.totalSteps,
            completion: core.selectStep({
              step: core.totalSteps,
              requestId: 0,
            }).completion!,
            executor: { select: async (req) => core.selectStep(req) },
            dispose() {
              if (session) {
                workerDisposed++;
                session = null;
              }
            },
          };
          return session;
        },
        dispose() {
          session?.dispose();
        },
      },
    },
    "ACTIVE",
  );
  const viewer = new ViewerController(),
    unbind = bindRunViewer(run, viewer),
    { store, history } = await controlled();
  history.accepted(accepted("OTHER"));
  store.finish();
  await drain();
  await run.refreshHealth();
  run.run();
  socket.ready();
  socket.accept();
  finish(socket, "ACTIVE");
  await drain();
  assert.equal(run.getSnapshot().trace.phase, "READY");
  viewer.setVisualizerConfig({ kind: "call_stack" });
  await drain();
  viewer.play();
  const before = requests.length;
  assert(openHistoryEntry(run, history, history.getSnapshot().entries[0]!.id));
  assert.equal(viewer.getSnapshot().phase, "EMPTY");
  assert.equal(viewer.getSnapshot().playing, false);
  assert.equal(viewer.getSnapshot().visualizerConfig.kind, "none");
  assert.equal(run.getSnapshot().trace.session, null);
  assert.equal(run.getSnapshot().editor.draftCode, "OTHER");
  assert.equal(run.getSnapshot().editor.mode, "editing");
  assert.equal(workerCreates, 1);
  assert.equal(workerDisposed, 1);
  assert.equal(requests.length, before);
  unbind();
  viewer.dispose();
  run.dispose();
  history.dispose();
});
test("side-effect lifecycle observer throwing cannot fail backend protocol", async () => {
  const h = setup("X");
  h.controller.subscribeLifecycle(() => {
    throw Error("observer failure");
  });
  await h.controller.refreshHealth();
  h.controller.run();
  const socket = h.sockets[0]!;
  socket.ready();
  socket.accept();
  socket.status("RUNNING");
  assert.equal(h.controller.getSnapshot().active.status, "RUNNING");
  assert.equal(h.controller.getSnapshot().transportError, null);
  h.controller.dispose();
});
test("Run dispose closes History connection and removes subscriptions", async () => {
  const h = setup(),
    { store, history } = await controlled();
  bindRunHistory(h.controller, history);
  h.controller.dispose();
  assert(store.closed);
  history.accepted(accepted());
  assert.equal(store.accepts.length, 0);
});
test("versionchange makes History UNAVAILABLE while Run remains usable", async () => {
  const h = await fresh(),
    history = new HistoryController(h.store),
    run = setup();
  await history.initialize();
  await run.controller.refreshHealth();
  const request = h.factory.open("codetrace-history", 2);
  const other = await new Promise<IDBDatabase>((r) => {
    request.onsuccess = () => r(request.result);
  });
  assert.equal(history.getSnapshot().phase, "UNAVAILABLE");
  assert(history.getSnapshot().warning?.includes("Версия"));
  assert(run.controller.canRun());
  other.close();
  run.controller.dispose();
  history.dispose();
});
test("controller tie sorting uses createdAt then UUID rather than repository order", async () => {
  const store = new DelayedStore(),
    base = {
      code: "X",
      createdAt: T1,
      lastRunAt: T2,
      status: "SUCCEEDED" as const,
    };
  store.entries = [
    { ...base, id: "00000000-0000-4000-8000-000000000002" },
    { ...base, id: "00000000-0000-4000-8000-000000000001" },
    { ...base, id: "00000000-0000-4000-8000-000000000003", createdAt: T2 },
  ];
  const history = new HistoryController(store);
  await history.initialize();
  assert.deepEqual(
    history.getSnapshot().entries.map((e) => e.id.slice(-1)),
    ["3", "1", "2"],
  );
  history.dispose();
});
test("accepted during initialization is persisted after open without delaying Run", async () => {
  const store = new DelayedStore();
  let resolve: () => void = () => {};
  store.open = () =>
    new Promise<void>((r) => {
      resolve = r;
    });
  const run = setup("X"),
    history = new HistoryController(
      store,
      () => !run.controller.canLoadDraft(),
    );
  bindRunHistory(run.controller, history);
  const init = history.initialize();
  await run.controller.refreshHealth();
  run.controller.run();
  const socket = run.sockets[0]!;
  socket.ready();
  socket.accept();
  finish(socket, "X");
  assert.equal(run.controller.getSnapshot().active.status, "SUCCEEDED");
  assert.equal(store.accepts.length, 0);
  resolve();
  await init;
  await drain();
  store.finish();
  await drain();
  assert.equal(history.getSnapshot().entries[0]!.status, "SUCCEEDED");
  run.controller.dispose();
  history.dispose();
});
