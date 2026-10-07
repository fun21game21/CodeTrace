import assert from "node:assert/strict";
import test from "node:test";
import { ViewerController } from "../../src/viewer/controller.ts";
import { TraceCore } from "../../src/trace/index.ts";
import { bindRunViewer } from "../../src/viewer/binding.ts";
import { fixture } from "../trace/helpers.ts";
import { begun, finish, settled } from "../runs/helpers.ts";
import { attached, deferred, flush } from "./helpers.ts";

test("attach selects real Step 1 and publishes no partial view", async () => {
  const core = TraceCore.load(fixture("assignment-128").trace);
  const viewer = new ViewerController();
  viewer.attach(core);
  assert.equal(viewer.getSnapshot().phase, "ATTACHED");
  assert.equal(viewer.getSnapshot().pendingStep, 1);
  assert.equal(viewer.getSnapshot().committedView, null);
  await flush();
  const s = viewer.getSnapshot();
  assert.equal(s.phase, "READY");
  assert.equal(s.committedStep, 1);
  assert.deepEqual(
    s.committedView,
    core.selectStep({ step: 1, requestId: s.latestRequestId }),
  );
  viewer.dispose();
});
test("same-instance attach is idempotent", async () => {
  const h = await attached();
  const before = h.viewer.getSnapshot();
  h.viewer.attach(h.core);
  await flush();
  assert.equal(h.viewer.getSnapshot(), before);
  h.viewer.dispose();
});
for (const action of ["detach", "dispose"] as const)
  test(action + " clears all committed/pending state and timers", async () => {
    const h = await attached();
    h.viewer.play();
    h.viewer.sliderInput(3);
    h.viewer[action]();
    h.clock.advance(5000);
    await flush();
    const s = h.viewer.getSnapshot();
    assert.equal(s.phase, "EMPTY");
    assert.equal(s.committedView, null);
    assert.equal(s.committedStep, null);
    assert.equal(s.pendingStep, null);
    assert.equal(s.selectedFrameId, null);
    assert.equal(s.selectionError, null);
    assert.equal(s.playing, false);
    assert.equal(h.clock.timers.size, 0);
  });
test("replacing trace invalidates old response even for the same RunId", async () => {
  const a = TraceCore.load(fixture("assignment-128").trace);
  const b = TraceCore.load(fixture("recursion-128").trace);
  const da = deferred(a),
    db = deferred(b);
  const viewer = new ViewerController({
    executor: (core) => (core === a ? da.executor : db.executor),
  });
  viewer.attach(a);
  await flush();
  viewer.attach(b);
  await flush();
  da.resolve();
  await flush();
  assert.equal(viewer.getSnapshot().committedView, null);
  db.resolve();
  await flush();
  assert(viewer.isAttached(b));
  assert.deepEqual(
    viewer.getSnapshot().committedView!.state,
    b.selectStep({ step: 1, requestId: 0 }).state,
  );
  assert.deepEqual(
    viewer.getSnapshot().committedView!.event,
    b.selectStep({ step: 1, requestId: 0 }).event,
  );
  viewer.dispose();
});
test("detached pending result cannot reattach", async () => {
  const h = await attached("frame_changes", true);
  h.viewer.seek(4);
  await flush();
  h.viewer.detach();
  h.d.resolve(1);
  await flush();
  assert.equal(h.viewer.getSnapshot().phase, "EMPTY");
});
test("disposed controller cannot attach or schedule", async () => {
  const h = await attached();
  h.viewer.dispose();
  h.viewer.attach(h.core);
  assert.equal(h.viewer.play(), false);
  assert.equal(h.viewer.seek(2), false);
  assert.equal(h.viewer.getSnapshot().committedView, null);
});
for (const text of ["2", "3", "0002"])
  test("direct valid integer draft " + text, async () => {
    const h = await attached();
    const old = h.viewer.getSnapshot().committedView;
    h.viewer.setStepDraft(text);
    assert.equal(h.viewer.getSnapshot().committedView, old);
    assert(h.viewer.commitStepDraft());
    await flush();
    assert.equal(h.viewer.getSnapshot().committedStep, Number(text));
    h.viewer.dispose();
  });
for (const text of [
  "",
  " ",
  " 2 ",
  "0",
  "-1",
  "1.5",
  "NaN",
  "Infinity",
  "1e2",
  "+2",
  "999999",
  "9007199254740993",
])
  test(
    "invalid Step never selects and reverts: " + JSON.stringify(text),
    async () => {
      const h = await attached("frame_changes", true),
        calls = h.d.calls.length;
      h.viewer.setStepDraft(text);
      assert.equal(h.viewer.commitStepDraft(), false);
      assert.equal(h.d.calls.length, calls);
      assert.equal(h.viewer.getSnapshot().directStepDraft, "1");
      assert(h.viewer.getSnapshot().stepValidationMessage);
      h.viewer.dispose();
    },
  );
test("Enter followed by blur coalesces the same direct target", async () => {
  const h = await attached("frame_changes", true);
  h.viewer.setStepDraft("4");
  h.viewer.commitStepDraft();
  h.viewer.commitStepDraft();
  await flush();
  assert.equal(h.d.calls.length, 2);
  h.d.resolve(1);
  await flush();
  h.viewer.commitStepDraft();
  await flush();
  assert.equal(h.d.calls.length, 2);
  h.viewer.dispose();
});
test("Previous boundary is no-op and Next moves once", async () => {
  const h = await attached();
  assert.equal(h.viewer.previous(), false);
  h.viewer.next();
  await flush();
  assert.equal(h.viewer.getSnapshot().committedStep, 2);
  h.viewer.previous();
  await flush();
  assert.equal(h.viewer.getSnapshot().committedStep, 1);
  h.viewer.dispose();
});
test("Next at final is no-op, Previous works", async () => {
  const h = await attached();
  h.viewer.seek(h.core.totalSteps);
  await flush();
  const before = h.viewer.getSnapshot().latestRequestId;
  assert.equal(h.viewer.next(), false);
  assert.equal(h.viewer.play(), false);
  assert.equal(h.viewer.getSnapshot().latestRequestId, before);
  h.viewer.previous();
  await flush();
  assert.equal(h.viewer.getSnapshot().committedStep, h.core.totalSteps - 1);
  h.viewer.dispose();
});
for (const step of [0, -1, 1.5, NaN, Infinity, 10000])
  test("API seek rejects invalid target " + step, async () => {
    const h = await attached();
    const s = h.viewer.getSnapshot();
    assert.equal(h.viewer.seek(step), false);
    assert.equal(h.viewer.getSnapshot(), s);
    h.viewer.dispose();
  });
test("manual requests monotonically increase IDs and keep only latest desired target", async () => {
  const h = await attached("frame_changes", true);
  h.viewer.seek(2);
  await flush();
  h.viewer.seek(3);
  const id = h.viewer.getSnapshot().latestRequestId;
  h.viewer.seek(4);
  assert(h.viewer.getSnapshot().latestRequestId > id);
  assert.equal(h.d.calls.length, 2);
  h.d.resolve(1);
  await flush();
  assert.equal(h.d.calls.at(-1)!.step, 4);
  assert.equal(h.viewer.getSnapshot().committedStep, 1);
  h.d.resolve(2);
  await flush();
  assert.equal(h.viewer.getSnapshot().committedStep, 4);
  h.viewer.dispose();
});
for (const obsolete of ["result", "error"] as const)
  test("stale " + obsolete + " ignored in full", async () => {
    const h = await attached("frame_changes", true),
      old = h.viewer.getSnapshot().committedView;
    h.viewer.seek(2);
    await flush();
    h.viewer.seek(4);
    if (obsolete === "result") h.d.resolve(1);
    else h.d.reject(1);
    await flush();
    assert.equal(h.viewer.getSnapshot().committedView, old);
    assert.equal(h.viewer.getSnapshot().selectionError, null);
    h.d.resolve(2);
    await flush();
    assert.equal(h.viewer.getSnapshot().committedStep, 4);
    h.viewer.dispose();
  });
test("pending keeps all panels, scope and highlight on old immutable view", async () => {
  const h = await attached("frame_changes", true),
    old = h.viewer.getSnapshot().committedView!;
  h.viewer.seek(4);
  await flush();
  const s = h.viewer.getSnapshot();
  assert.equal(s.committedView, old);
  assert.equal(s.selectedFrameId, old.selectedFrameId);
  assert.equal(s.committedStep, old.step);
  assert(Object.isFrozen(s));
  assert(Object.isFrozen(old));
  h.viewer.dispose();
});
test("one complete selection publication has consistent panel references", async () => {
  const h = await attached("frame_changes", true),
    observations: number[] = [];
  h.viewer.subscribe(() => {
    const s = h.viewer.getSnapshot(),
      v = s.committedView;
    if (v === null) {
      assert.equal(s.phase, "EMPTY");
      return;
    }
    assert.equal(s.committedStep, v.step);
    assert.equal(s.selectedFrameId, v.selectedFrameId);
    observations.push(v.requestId);
  });
  h.viewer.seek(4);
  await flush();
  h.d.resolve(1);
  await flush();
  assert(observations.includes(h.viewer.getSnapshot().latestRequestId));
  h.viewer.dispose();
});
test("selection failure preserves old view and stops Play", async () => {
  const h = await attached("frame_changes", true),
    old = h.viewer.getSnapshot().committedView;
  h.viewer.play();
  h.clock.advance(200);
  await flush();
  h.d.reject(1);
  await flush();
  assert.equal(h.viewer.getSnapshot().committedView, old);
  assert.equal(h.viewer.getSnapshot().playing, false);
  assert.equal(h.viewer.getSnapshot().pendingStep, null);
  assert(h.viewer.getSnapshot().selectionError);
  assert.equal(h.clock.timers.size, 0);
  h.viewer.dispose();
});
test("mismatched executor request ID is never published", async () => {
  const h = await attached("frame_changes", true),
    old = h.viewer.getSnapshot().committedView;
  h.viewer.seek(3);
  await flush();
  h.d.pending[1]!.resolve(h.core.selectStep({ step: 3, requestId: 9999 }));
  await flush();
  assert.equal(h.viewer.getSnapshot().committedView, old);
  assert(h.viewer.getSnapshot().selectionError);
  h.viewer.dispose();
});
test("synchronous executor throw follows neutral failure path", async () => {
  const core = TraceCore.load(fixture("assignment-128").trace);
  const viewer = new ViewerController({
    executor: () => ({
      select: () => {
        throw new Error("sync");
      },
    }),
  });
  viewer.attach(core);
  await flush();
  assert.equal(viewer.getSnapshot().committedView, null);
  assert(viewer.getSnapshot().selectionError);
  assert(viewer.isAttached(core));
  viewer.dispose();
});
test("slider uses trailing 50ms throttle and latest raw target", async () => {
  const h = await attached("frame_changes", true),
    old = h.viewer.getSnapshot().committedView;
  h.viewer.sliderInput(2);
  h.clock.advance(20);
  h.viewer.sliderInput(3);
  h.clock.advance(29);
  await flush();
  assert.equal(h.d.calls.length, 1);
  assert.equal(h.viewer.getSnapshot().sliderTarget, 3);
  assert.equal(h.viewer.getSnapshot().committedView, old);
  h.clock.advance(1);
  await flush();
  assert.equal(h.d.calls.at(-1)!.step, 3);
  h.d.resolve(1);
  await flush();
  assert.equal(h.viewer.getSnapshot().committedStep, 3);
  h.viewer.dispose();
});
test("slider release immediately flushes final target", async () => {
  const h = await attached("frame_changes", true);
  h.viewer.sliderInput(2);
  h.viewer.sliderInput(3);
  h.viewer.sliderCommit(4);
  await flush();
  assert.equal(h.d.calls.at(-1)!.step, 4);
  assert.equal(h.clock.timers.size, 0);
  h.d.resolve(1);
  await flush();
  assert.equal(h.viewer.getSnapshot().committedStep, 4);
  h.viewer.dispose();
});
test("slow slider coalesces a burst without a queue and executes final release", async () => {
  const h = await attached("frame_changes", true);
  h.viewer.sliderInput(2);
  h.clock.advance(50);
  await flush();
  h.viewer.sliderInput(3);
  h.clock.advance(50);
  h.viewer.sliderInput(5);
  h.viewer.sliderCommit(4);
  await flush();
  assert.equal(h.d.calls.length, 2);
  h.d.resolve(1);
  await flush();
  assert.equal(h.d.calls.length, 3);
  assert.equal(h.d.calls[2]!.step, 4);
  h.d.resolve(2);
  await flush();
  assert.equal(h.viewer.getSnapshot().committedStep, 4);
  h.viewer.dispose();
});
test("in-flight slider publication cannot erase a newer unflushed thumb target", async () => {
  const h = await attached("frame_changes", true);
  h.viewer.sliderInput(2);
  h.clock.advance(50);
  await flush();
  h.viewer.sliderInput(4);
  h.d.resolve(1);
  await flush();
  assert.equal(h.viewer.getSnapshot().sliderTarget, 4);
  h.clock.advance(50);
  await flush();
  assert.equal(h.d.calls.at(-1)!.step, 4);
  h.d.resolve(2);
  await flush();
  assert.equal(h.viewer.getSnapshot().committedStep, 4);
  h.viewer.dispose();
});
test("slider drag pauses and release never resumes Play", async () => {
  const h = await attached();
  h.viewer.play();
  h.viewer.sliderInput(3);
  assert.equal(h.viewer.getSnapshot().playing, false);
  h.viewer.sliderCommit(3);
  await flush();
  h.clock.advance(2000);
  await flush();
  assert.equal(h.viewer.getSnapshot().committedStep, 3);
  h.viewer.dispose();
});
test("reverse slider reconstructs earlier Step", async () => {
  const h = await attached();
  h.viewer.seek(4);
  await flush();
  h.viewer.sliderCommit(2);
  await flush();
  assert.equal(h.viewer.getSnapshot().committedStep, 2);
  h.viewer.dispose();
});
test("default Play speed is five", async () => {
  const h = await attached();
  assert.equal(h.viewer.getSnapshot().speed, 5);
  h.viewer.dispose();
});
for (const speed of [1, 2, 5, 10, 20])
  test("Play interval at speed " + speed, async () => {
    const h = await attached();
    assert(h.viewer.setSpeed(speed));
    assert(h.viewer.play());
    h.clock.advance(1000 / speed - 1);
    await flush();
    assert.equal(h.viewer.getSnapshot().committedStep, 1);
    h.clock.advance(1);
    await flush();
    assert.equal(h.viewer.getSnapshot().committedStep, 2);
    assert.equal(h.clock.timers.size, 1);
    h.viewer.dispose();
  });
for (const speed of [0, -1, 3, 4, 100, NaN, Infinity])
  test("invalid speed rejected " + speed, async () => {
    const h = await attached();
    const old = h.viewer.getSnapshot();
    assert.equal(h.viewer.setSpeed(speed), false);
    assert.equal(h.viewer.getSnapshot(), old);
    h.viewer.dispose();
  });
test("slow reconstruction does not overlap or accumulate Play ticks", async () => {
  const h = await attached("frame_changes", true);
  h.viewer.play();
  h.clock.advance(200);
  await flush();
  h.clock.advance(10000);
  await flush();
  assert.equal(h.d.calls.length, 2);
  assert.equal(h.clock.timers.size, 0);
  h.d.resolve(1);
  await flush();
  assert.equal(h.viewer.getSnapshot().committedStep, 2);
  h.clock.advance(199);
  await flush();
  assert.equal(h.d.calls.length, 2);
  h.clock.advance(1);
  await flush();
  assert.equal(h.d.calls.at(-1)!.step, 3);
  h.viewer.dispose();
});
test("Pause cancels timer", async () => {
  const h = await attached();
  h.viewer.play();
  h.viewer.pause();
  h.clock.advance(5000);
  await flush();
  assert.equal(h.viewer.getSnapshot().committedStep, 1);
  assert.equal(h.clock.timers.size, 0);
  h.viewer.dispose();
});
test("Pause during pending Play may commit but never restarts loop", async () => {
  const h = await attached("frame_changes", true);
  h.viewer.play();
  h.clock.advance(200);
  await flush();
  h.viewer.pause();
  h.d.resolve(1);
  await flush();
  h.clock.advance(5000);
  await flush();
  assert.equal(h.viewer.getSnapshot().committedStep, 2);
  assert.equal(h.viewer.getSnapshot().playing, false);
  assert.equal(h.d.calls.length, 2);
  h.viewer.dispose();
});
test("Play stops at final RUN_END", async () => {
  const h = await attached();
  h.viewer.seek(h.core.totalSteps - 1);
  await flush();
  h.viewer.play();
  h.clock.advance(200);
  await flush();
  assert.equal(h.viewer.getSnapshot().committedView!.event.kind, "RUN_END");
  assert.equal(h.viewer.getSnapshot().playing, false);
  assert.equal(h.clock.timers.size, 0);
  h.viewer.dispose();
});
test("speed changes reschedule next interval without selecting state", async () => {
  const h = await attached();
  h.viewer.play();
  h.clock.advance(50);
  const old = h.viewer.getSnapshot().committedView;
  h.viewer.setSpeed(20);
  assert.equal(h.viewer.getSnapshot().committedView, old);
  h.clock.advance(49);
  await flush();
  assert.equal(h.viewer.getSnapshot().committedStep, 1);
  h.clock.advance(1);
  await flush();
  assert.equal(h.viewer.getSnapshot().committedStep, 2);
  h.viewer.dispose();
});
test("speed changed in-flight governs interval after publication", async () => {
  const h = await attached("frame_changes", true);
  h.viewer.play();
  h.clock.advance(200);
  await flush();
  h.viewer.setSpeed(1);
  h.d.resolve(1);
  await flush();
  h.clock.advance(999);
  await flush();
  assert.equal(h.d.calls.length, 2);
  h.clock.advance(1);
  await flush();
  assert.equal(h.d.calls.length, 3);
  h.viewer.dispose();
});
for (const frame of ["f1", "f2", null] as const)
  test("atomic frame/Globals selection " + frame, async () => {
    const h = await attached();
    h.viewer.seek(4);
    await flush();
    const previous = h.viewer.getSnapshot().committedView,
      id = h.viewer.getSnapshot().latestRequestId;
    h.viewer.selectFrame(frame);
    assert.equal(h.viewer.getSnapshot().committedView, previous);
    await flush();
    const s = h.viewer.getSnapshot();
    assert.equal(s.selectedFrameId, frame);
    assert.equal(s.committedStep, 4);
    assert(s.latestRequestId > id);
    assert.deepEqual(
      s.committedView,
      h.core.selectStep({
        step: 4,
        requestId: s.latestRequestId,
        requestedFrameId: frame,
      }),
    );
    h.viewer.dispose();
  });
test("caller persists across Steps; removed selected frame falls back to top", async () => {
  const h = await attached();
  h.viewer.seek(4);
  await flush();
  h.viewer.selectFrame("f2");
  await flush();
  h.viewer.seek(5);
  await flush();
  assert.equal(h.viewer.getSnapshot().selectedFrameId, "f2");
  h.viewer.seek(4);
  await flush();
  h.viewer.selectFrame("f3");
  await flush();
  h.viewer.seek(5);
  await flush();
  assert.equal(h.viewer.getSnapshot().selectedFrameId, "f2");
  h.viewer.dispose();
});
test("empty stack resolves to Globals; explicit Globals persist", async () => {
  const h = await attached();
  h.viewer.selectFrame(null);
  await flush();
  h.viewer.seek(4);
  await flush();
  assert.equal(h.viewer.getSnapshot().selectedFrameId, null);
  h.viewer.seek(h.core.totalSteps);
  await flush();
  assert.equal(h.viewer.getSnapshot().selectedFrameId, null);
  h.viewer.dispose();
});
test("recursive same-name frames retain distinct IDs and recorded order", async () => {
  const h = await attached("recursion");
  const input = fixture("recursion-128");
  const index = input.expected.findIndex((v) => v.callStack.length >= 4);
  h.viewer.seek(index + 1);
  await flush();
  const stack = h.viewer.getSnapshot().committedView!.callStack;
  assert.equal(new Set(stack.map((f) => f.id)).size, stack.length);
  assert(new Set(stack.map((f) => f.name)).size < stack.length);
  h.viewer.dispose();
});
test("Run ownership binding attaches, Edit detaches, navigation makes no HTTP/start calls", async () => {
  const run = await begun(),
    viewer = new ViewerController(),
    unbind = bindRunViewer(run.controller, viewer);
  finish(run.socket);
  await settled();
  await flush();
  assert.equal(viewer.getSnapshot().committedStep, 1);
  const calls = run.calls.length,
    starts = run.socket.sent.length;
  viewer.next();
  await flush();
  viewer.previous();
  await flush();
  viewer.seek(3);
  await flush();
  assert.equal(run.calls.length, calls);
  assert.equal(run.socket.sent.length, starts);
  run.controller.edit();
  assert.equal(viewer.getSnapshot().phase, "EMPTY");
  unbind();
  viewer.dispose();
  run.controller.dispose();
});
test("RunController disposal detaches pending/playing Viewer", async () => {
  const run = await begun(),
    viewer = new ViewerController(),
    unbind = bindRunViewer(run.controller, viewer);
  finish(run.socket);
  await settled();
  viewer.play();
  run.controller.dispose();
  assert.equal(viewer.getSnapshot().phase, "EMPTY");
  assert.equal(viewer.getSnapshot().playing, false);
  unbind();
  viewer.dispose();
});
test("already disposed RunController cannot attach its old core", async () => {
  const run = await begun();
  finish(run.socket);
  await settled();
  run.controller.dispose();
  const viewer = new ViewerController(),
    unbind = bindRunViewer(run.controller, viewer);
  assert.equal(viewer.getSnapshot().phase, "EMPTY");
  unbind();
  viewer.dispose();
});
test("only READY and initial committed view enable controls", () => {
  const viewer = new ViewerController();
  assert.equal(viewer.next(), false);
  assert.equal(viewer.previous(), false);
  assert.equal(viewer.seek(1), false);
  assert.equal(viewer.play(), false);
  assert.equal(viewer.sliderInput(1), false);
  assert.equal(viewer.selectFrame(null), false);
  viewer.dispose();
});
