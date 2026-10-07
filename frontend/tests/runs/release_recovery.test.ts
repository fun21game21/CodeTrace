import assert from "node:assert/strict";
import test from "node:test";
import { ViewerController } from "../../src/viewer/controller.ts";
import { bindRunViewer } from "../../src/viewer/binding.ts";
import {
  HistoryController,
  openHistoryEntry,
} from "../../src/history/index.ts";
import { DelayedStore, T1 } from "../history/helpers.ts";
import { REQUEST, RUN, settled } from "./helpers.ts";
import { recoveryHarness, FA01_CODE } from "./release_recovery_helpers.ts";
import type { HttpResult } from "../../src/runs/types.ts";

async function failed(t: { after(fn: () => void): void }) {
  const h = recoveryHarness();
  t.after(() => h.run.dispose());
  await h.complete();
  assert.equal(h.run.getSnapshot().trace.phase, "LOAD_FAILED");
  assert.equal(h.posts.length, 0);
  return h;
}
test("release recovery failed load → Edit releases, clears presentation and enables Run without reload", async (t) => {
  const h = await failed(t),
    viewer = new ViewerController(),
    unbind = bindRunViewer(h.run, viewer);
  t.after(() => {
    unbind();
    viewer.dispose();
  });
  h.run.edit();
  assert.equal(h.run.getSnapshot().editor.mode, "editing");
  assert.equal(h.run.getSnapshot().editor.draftCode, FA01_CODE);
  assert.equal(h.run.getSnapshot().trace.phase, "EMPTY");
  assert.equal(viewer.getSnapshot().phase, "EMPTY");
  assert.equal(h.run.getSnapshot().trace.session, null);
  assert.equal(h.counts().liveWorkers, 0);
  assert.equal(h.run.canRun(), false, "204 has not arrived yet");
  assert.deepEqual(h.posts, [`/api/runs/${RUN}/release`]);
  await settled();
  assert(h.run.canRun());
  assert.equal(h.run.getSnapshot().trace.releasePending, false);
  assert.equal(h.sockets[0]!.sent.length, 1);
});
test("release recovery failed load → History Open preserves exact other source and does not execute", async (t) => {
  const h = await failed(t),
    store = new DelayedStore(),
    history = new HistoryController(store);
  const code = "  # history\r\nprint('other')\r\n",
    id = crypto.randomUUID();
  store.entries = [
    { id, code, createdAt: T1, lastRunAt: T1, status: "SUCCEEDED" },
  ];
  await history.initialize();
  t.after(() => history.dispose());
  assert(openHistoryEntry(h.run, history, id));
  assert.equal(h.run.getSnapshot().editor.draftCode, code);
  await settled();
  assert(h.run.canRun());
  assert.equal(h.counts().liveWorkers, 0);
  assert.equal(h.posts.length, 1);
  assert.equal(h.sockets.length, 1);
  assert.equal(h.sockets[0]!.sent.length, 1);
});
for (const error of ["network", 500, 409] as const)
  test(`release recovery release ${error} keeps ownership/draft/Run disabled; explicit retry 204 recovers`, async (t) => {
    const h = await failed(t);
    h.setRelease(async () => {
      if (error === "network") throw Error("network");
      return { status: error, json: async () => null };
    });
    h.run.edit();
    h.run.setDraft("edited draft");
    await settled();
    assert.equal(h.run.getSnapshot().editor.mode, "editing");
    assert.equal(h.run.getSnapshot().editor.draftCode, "edited draft");
    assert(h.run.getSnapshot().trace.releasePending);
    assert(h.run.getSnapshot().trace.releaseError);
    assert.equal(h.run.canRun(), false);
    assert.equal(h.run.run(), false);
    h.setRelease(async () => ({ status: 204, json: async () => null }));
    await h.run.retryRelease();
    assert(h.run.canRun());
    assert.equal(h.run.getSnapshot().trace.releaseError, null);
    assert.equal(h.run.getSnapshot().trace.releasePending, false);
    assert.equal(h.run.getSnapshot().editor.draftCode, "edited draft");
    assert.equal(h.posts.length, 2);
  });
test("release recovery expired/already released result 204 frees controller without health refresh", async (t) => {
  const h = await failed(t);
  h.setRelease(async () => ({
    status: 204,
    json: async () => {
      throw Error("204 has no body");
    },
  }));
  h.run.edit();
  await settled();
  assert(h.run.canRun());
  assert.equal(h.posts.length, 1);
});
test("release recovery in-flight release coalesces repeated Edit, loadDraft and Retry", async (t) => {
  const h = await failed(t);
  let resolve!: (r: HttpResult) => void;
  h.setRelease(
    () =>
      new Promise((r) => {
        resolve = r;
      }),
  );
  h.run.edit();
  h.run.edit();
  assert(h.run.loadDraft("other"));
  await h.run.retryRelease();
  await h.run.retryRelease();
  assert.equal(h.posts.length, 1);
  assert.equal(h.run.canRun(), false);
  resolve({ status: 204, json: async () => null });
  await settled();
  assert(h.run.canRun());
  await h.run.retryRelease();
  assert.equal(h.posts.length, 1);
  assert.equal(h.run.getSnapshot().editor.draftCode, "other");
});
test("release recovery Retry Download before Edit keeps normal READY/release path once", async (t) => {
  const h = await failed(t);
  assert(h.run.canRetryDownload());
  h.setLoadFailure(false);
  await h.run.download();
  assert.equal(h.run.getSnapshot().trace.phase, "READY");
  assert(h.run.getSnapshot().trace.session);
  assert.equal(h.posts.length, 1);
  assert.equal(h.counts().loads, 2);
  h.run.edit();
  await settled();
  assert.equal(h.posts.length, 1);
  assert(h.run.canRun());
});
test("release recovery normal success and READY Edit still have exactly one release", async (t) => {
  const h = recoveryHarness();
  t.after(() => h.run.dispose());
  h.setLoadFailure(false);
  await h.complete();
  assert.equal(h.run.getSnapshot().trace.phase, "READY");
  assert.equal(h.posts.length, 1);
  h.run.edit();
  await settled();
  assert(h.run.canRun());
  assert.equal(h.posts.length, 1);
});
test("release recovery Edit during normal READY release does not duplicate in-flight POST", async (t) => {
  const h = recoveryHarness();
  t.after(() => h.run.dispose());
  let resolve!: (r: HttpResult) => void;
  h.setRelease(
    () =>
      new Promise((r) => {
        resolve = r;
      }),
  );
  h.setLoadFailure(false);
  await h.complete();
  h.run.edit();
  await h.run.retryRelease();
  assert.equal(h.posts.length, 1);
  assert(!h.run.canRun());
  resolve({ status: 204, json: async () => null });
  await settled();
  assert(h.run.canRun());
});
test("release recovery no-trace completion and Edit do not release", async (t) => {
  const h = recoveryHarness();
  t.after(() => h.run.dispose());
  await h.complete(false);
  h.run.edit();
  await settled();
  assert.equal(h.posts.length, 0);
  assert.equal(h.counts().loads, 0);
  assert(h.run.canRun());
});
test("release recovery pre-accept rejection does not release", async (t) => {
  const h = recoveryHarness();
  t.after(() => h.run.dispose());
  await h.run.refreshHealth();
  assert(h.run.run());
  const socket = h.sockets[0]!;
  socket.ready();
  socket.emit(
    "run_rejected",
    { clientRequestId: REQUEST, code: "RUN_BUSY", message: "busy" },
    null,
  );
  h.run.edit();
  assert(h.run.loadDraft("other"));
  await h.run.retryRelease();
  assert.equal(h.posts.length, 0);
  assert.equal(h.counts().loads, 0);
  assert(h.run.canRun());
});
test("release recovery recovered controller starts next source exactly once", async (t) => {
  const h = await failed(t);
  h.run.loadDraft("print(2)");
  await settled();
  h.setLoadFailure(false);
  await h.complete();
  assert.equal(h.run.getSnapshot().active.completion!.outcome, "SUCCESS");
  assert.equal(h.sockets.length, 2);
  assert.deepEqual(
    h.sockets.map((s) => s.sent.length),
    [1, 1],
  );
  assert.equal(JSON.parse(h.sockets[1]!.sent[0]!).payload.code, "print(2)");
  assert.equal(h.posts.length, 2);
});
