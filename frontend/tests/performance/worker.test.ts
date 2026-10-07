import assert from "node:assert/strict";
import test from "node:test";
import { makeFixture } from "./fixtures.ts";
import { TraceCore } from "../../src/trace/index.ts";
import { WorkerSession } from "../../src/execution/worker_session.ts";
import { freezeView } from "../../src/execution/freeze.ts";
import { ViewerController } from "../../src/viewer/controller.ts";
import { bindRunViewer } from "../../src/viewer/binding.ts";
import { RunController } from "../../src/runs/controller.ts";
import { manifest, fixture } from "../trace/helpers.ts";
import {
  validTrace,
  health,
  Socket,
  REQUEST,
  finish,
  settled,
} from "../runs/helpers.ts";
import { flush } from "../viewer/helpers.ts";
import { FakeWorker, workerHarness } from "./worker_helpers.ts";
import type { TraceLoadRequest } from "../../src/execution/types.ts";
const expected = (trace = validTrace()): TraceLoadRequest => ({
  traceUrl: "http://127.0.0.1/trace",
  expectedRunId: trace.runId,
  expectedSourceCode: trace.sourceCode,
  expectedEventCount: trace.events.length,
});
function fakeLoaded(worker: FakeWorker, id = "session", trace = validTrace()) {
  worker.reply({
    type: "loaded",
    sessionId: id,
    runId: trace.runId,
    sourceCode: trace.sourceCode,
    totalSteps: trace.events.length,
    validated: true,
    completion: trace.completion,
    timing: { fetchMs: 1, jsonParseMs: 2, traceCoreLoadMs: 3 },
  });
}
for (const entry of manifest)
  test(
    "isolated Worker equals all Steps of TRACE oracle " +
      entry.file.replace(/\.json$/, ""),
    async () => {
      const h = await workerHarness();
      try {
        const t = fixture(entry.file.replace(/\.json$/, "")).trace;
        h.setTrace(t);
        const session = await h.loader.load(h.request(t)),
          direct = TraceCore.load(t);
        for (const step of Array.from(
          { length: direct.totalSteps },
          (_, i) => i + 1,
        )) {
          const request = { step, requestId: step };
          const actual = await session.executor.select(request);
          assert.deepEqual(actual, direct.selectStep(request));
          assert(Object.isFrozen(actual.state.objects));
        }
        for (const requestedFrameId of [null, "f1", "f9999"]) {
          const request = {
            step: Math.max(1, direct.totalSteps - 1),
            requestId: 100000,
            requestedFrameId,
          };
          assert.deepEqual(
            await session.executor.select(request),
            direct.selectStep(request),
          );
        }
        assert.equal(h.counts().gets, 1);
        assert.equal(h.counts().peak, 1);
        assert(h.commands.every((c) => !("trace" in c) && !("body" in c)));
        assert.equal(h.commands.filter((c) => c.type === "load").length, 1);
      } finally {
        await h.close();
      }
    },
  );
for (const damage of [
  "runId",
  "source",
  "eventCount",
  "HTTP",
  "JSON",
  "schema",
] as const)
  test(
    "Worker rejects " + damage + " and retry loads without new Run",
    async () => {
      const h = await workerHarness();
      try {
        const t = validTrace();
        h.setTrace(t);
        const request = h.request(t);
        if (damage === "runId") request.expectedRunId = REQUEST;
        if (damage === "source") request.expectedSourceCode = "wrong";
        if (damage === "eventCount") request.expectedEventCount = 999;
        if (damage === "HTTP") h.httpFailure();
        if (damage === "JSON") h.corrupt();
        if (damage === "schema") h.setTrace({ ...t, schemaVersion: "bad" });
        await assert.rejects(h.loader.load(request));
        assert.equal(h.counts().live, 0);
        h.setTrace(t);
        const loaded = await h.loader.load(h.request(t));
        assert.equal(loaded.totalSteps, 3);
        assert.equal(h.counts().gets, 2);
        assert.equal(h.counts().peak, 1);
      } finally {
        await h.close();
      }
    },
  );
test("new trace terminates old Worker before allocating replacement", async () => {
  const h = await workerHarness();
  try {
    const t = validTrace();
    h.setTrace(t);
    const first = await h.loader.load(h.request(t));
    await h.loader.load(h.request(t));
    await assert.rejects(first.executor.select({ step: 1, requestId: 1 }));
    assert.deepEqual(h.counts(), {
      gets: 2,
      created: 2,
      terminated: 1,
      live: 1,
      peak: 1,
    });
  } finally {
    await h.close();
  }
});
for (const action of ["dispose", "error", "wrong-id"] as const)
  test("pending selection safely rejects on " + action, async () => {
    const port = new FakeWorker(),
      session = new WorkerSession(port, "session", expected());
    fakeLoaded(port);
    await session.loaded;
    const result = session.executor.select({ step: 1, requestId: 4 });
    const rejection = assert.rejects(result);
    if (action === "dispose") session.dispose();
    else if (action === "error") port.error();
    else {
      const view = TraceCore.load(validTrace()).selectStep({
        step: 1,
        requestId: 5,
      });
      port.reply({
        type: "selected",
        sessionId: "session",
        requestId: 5,
        selectionMs: 1,
        view,
      });
    }
    await rejection;
    assert(port.terminated);
  });
test("late previous attachment message cannot publish in the new session", async () => {
  const port = new FakeWorker(),
    session = new WorkerSession(port, "session", expected());
  fakeLoaded(port);
  await session.loaded;
  const p = session.executor.select({ step: 1, requestId: 7 });
  let resolved = false;
  void p.then(() => {
    resolved = true;
  });
  const view = TraceCore.load(validTrace()).selectStep({
    step: 1,
    requestId: 7,
  });
  port.reply({
    type: "selected",
    sessionId: "old",
    requestId: 7,
    selectionMs: 1,
    view,
  });
  await flush();
  assert(!resolved);
  port.reply({
    type: "selected",
    sessionId: "session",
    requestId: 7,
    selectionMs: 1,
    view,
  });
  assert.equal((await p).requestId, 7);
  session.dispose();
});
test("Viewer ignores obsolete Worker result and atomically commits newest target", async () => {
  const port = new FakeWorker(),
    session = new WorkerSession(port, "session", expected());
  fakeLoaded(port);
  await session.loaded;
  const core = TraceCore.load(validTrace()),
    viewer = new ViewerController();
  viewer.attach(session);
  await flush();
  const respond = (id: number, step: number) =>
    port.reply({
      type: "selected",
      sessionId: "session",
      requestId: id,
      selectionMs: 1,
      view: core.selectStep({ step, requestId: id }),
    });
  respond(1, 1);
  await flush();
  viewer.seek(2);
  await flush();
  viewer.seek(3);
  respond(2, 2);
  await flush();
  assert.equal(viewer.getSnapshot().committedStep, 1);
  respond(3, 3);
  await flush();
  assert.equal(viewer.getSnapshot().committedStep, 3);
  viewer.dispose();
  session.dispose();
});
test("structured clone of near-limit State is deeply immutable again", () => {
  const t = makeFixture("large-list").trace;
  const view = TraceCore.load(t).selectStep({ step: 500, requestId: 1 });
  const copy = structuredClone(view);
  assert(!Object.isFrozen(copy));
  freezeView(copy);
  assert.deepEqual(copy, view);
  assert(Object.isFrozen(copy.state.objects[0]));
  const object = copy.state.objects[0]!;
  assert(object.kind === "list");
  assert(Object.isFrozen(object.items[0]));
});
test("deep freeze handles alias identity and cycles iteratively", () => {
  const x: { a?: unknown; b?: unknown } = {};
  x.a = x;
  x.b = x;
  freezeView(x);
  assert(Object.isFrozen(x));
  assert.equal(x.a, x.b);
});
test("main ownership contains metadata/session, no TraceCore/body; release failure preserves usable Worker", async () => {
  const h = await workerHarness();
  const t = validTrace("pass");
  h.setTrace(t);
  const socket = new Socket();
  let releases = 0,
    failRelease = true,
    mainGets = 0;
  const run = new RunController(
    {
      uuid: () => REQUEST,
      socket: () => socket,
      traceLoader: {
        load: (r) => h.loader.load({ ...r, traceUrl: h.request(t).traceUrl }),
        dispose: () => h.loader.dispose(),
      },
      fetch: async (url, options) => {
        if (options) {
          releases++;
          return { status: failRelease ? 500 : 204, json: async () => null };
        }
        if (url !== "/api/health") mainGets++;
        return { status: 200, json: async () => health() };
      },
    },
    "pass",
  );
  const viewer = new ViewerController(),
    unbind = bindRunViewer(run, viewer);
  try {
    await run.refreshHealth();
    assert(run.run());
    socket.ready();
    socket.accept();
    socket.status("STARTING");
    finish(socket, "pass");
    await until(
      () =>
        run.getSnapshot().trace.phase === "READY" &&
        run.getSnapshot().trace.releaseError !== null,
    );
    const state = run.getSnapshot();
    assert.equal(state.trace.handle, null);
    assert(state.trace.session);
    assert.equal(mainGets, 0);
    assert.equal(releases, 1);
    assert(!("events" in state.trace.session));
    assert(!("states" in state.trace.session));
    assert(!("raw" in state.trace.session));
    await until(() => viewer.getSnapshot().committedStep === 1);
    viewer.seek(3);
    await until(() => viewer.getSnapshot().committedStep === 3);
    assert.equal(releases, 1);
    assert.equal(h.counts().gets, 1);
    failRelease = false;
    await run.retryRelease();
    assert.equal(run.getSnapshot().trace.session, state.trace.session);
    assert.equal(releases, 2);
    run.edit();
    assert.equal(viewer.getSnapshot().phase, "EMPTY");
    assert.equal(h.counts().live, 0);
    assert.equal(run.getSnapshot().editor.draftCode, "pass");
  } finally {
    unbind();
    viewer.dispose();
    run.dispose();
    await h.close();
  }
});
test("load failure does not release; explicit retry does not start Python", async () => {
  const h = await workerHarness(),
    socket = new Socket();
  const t = validTrace();
  h.setTrace(t);
  h.corrupt();
  let releases = 0;
  const run = new RunController({
    uuid: () => REQUEST,
    socket: () => socket,
    traceLoader: {
      load: (r) => h.loader.load({ ...r, traceUrl: h.request(t).traceUrl }),
      dispose: () => h.loader.dispose(),
    },
    fetch: async (_url, options) => {
      if (options) releases++;
      return { status: options ? 204 : 200, json: async () => health() };
    },
  });
  try {
    await run.refreshHealth();
    run.run();
    socket.ready();
    socket.accept();
    socket.status("STARTING");
    finish(socket);
    await until(() => run.getSnapshot().trace.phase === "LOAD_FAILED");
    assert.equal(releases, 0);
    h.setTrace(t);
    await run.download();
    assert.equal(run.getSnapshot().trace.phase, "READY");
    assert.equal(releases, 1);
    assert.equal(socket.sent.length, 1);
  } finally {
    run.dispose();
    await h.close();
  }
});
async function until(predicate: () => boolean) {
  const end = Date.now() + 10000;
  while (!predicate()) {
    if (Date.now() > end) throw Error("Test deadline");
    await settled();
  }
}
for (const damage of ["runId", "source", "count", "validated"] as const)
  test("main rejects mismatched loaded metadata " + damage, async () => {
    const port = new FakeWorker(),
      session = new WorkerSession(port, "session", expected());
    const rejected = assert.rejects(session.loaded);
    const t = validTrace();
    const reply = {
      type: "loaded" as const,
      sessionId: "session",
      runId: t.runId,
      sourceCode: t.sourceCode,
      totalSteps: 3,
      validated: true as const,
      completion: t.completion,
      timing: { fetchMs: 0, jsonParseMs: 0, traceCoreLoadMs: 0 },
    };
    if (damage === "runId") reply.runId = REQUEST;
    if (damage === "source") reply.sourceCode = "wrong";
    if (damage === "count") reply.totalSteps = 7;
    if (damage === "validated") Object.assign(reply, { validated: false });
    port.reply(reply);
    await rejected;
    assert(port.terminated);
  });
test("dispose during load rejects outstanding ownership without READY", async () => {
  const port = new FakeWorker(),
    session = new WorkerSession(port, "session", expected());
  const rejected = assert.rejects(session.loaded);
  session.dispose();
  await rejected;
  fakeLoaded(port);
  assert(port.terminated);
  await assert.rejects(session.executor.select({ step: 1, requestId: 1 }));
});
test("worker selection error is neutral, contains no source or stack, session remains usable", async () => {
  const h = await workerHarness();
  try {
    const t = validTrace("secret source");
    h.setTrace(t);
    const session = await h.loader.load(h.request(t));
    await assert.rejects(
      session.executor.select({ step: 0, requestId: 2 }),
      (e) => {
        assert(e instanceof Error);
        assert.equal(e.message, "Не удалось выбрать шаг.");
        return true;
      },
    );
    assert.equal(
      (await session.executor.select({ step: 1, requestId: 3 })).step,
      1,
    );
  } finally {
    await h.close();
  }
});
test("RunController dispose during Worker load terminates owner and never releases", async () => {
  const port = new FakeWorker(),
    loader = new (
      await import("../../src/execution/worker_session.ts")
    ).WorkerTraceLoader(
      () => port,
      () => "session",
    );
  const socket = new Socket();
  let releases = 0;
  const run = new RunController({
    traceLoader: loader,
    uuid: () => REQUEST,
    socket: () => socket,
    fetch: async (_url, options) => {
      if (options) releases++;
      return { status: 200, json: async () => health() };
    },
  });
  await run.refreshHealth();
  run.run();
  socket.ready();
  socket.accept();
  socket.status("STARTING");
  finish(socket);
  run.dispose();
  fakeLoaded(port);
  await settled();
  assert(port.terminated);
  assert.equal(releases, 0);
  assert.notEqual(run.getSnapshot().trace.phase, "READY");
});

test("production Worker has profiling disabled by default", async () => {
  const h = await workerHarness();
  try {
    const trace = validTrace();
    h.setTrace(trace);
    const session = await h.loader.load(h.request(trace));
    assert(session instanceof WorkerSession);
    assert.equal(session.loadTiming, null);
    const command = h.commands[0]!;
    assert.equal(command.type, "load");
    if (command.type === "load") assert.equal(command.measure, undefined);
    await session.executor.select({ step: 1, requestId: 8 });
    assert.equal(session.lastFreezeMs, 0);
    assert.equal(session.lastSelectionMs, 0);
  } finally {
    await h.close();
  }
});
