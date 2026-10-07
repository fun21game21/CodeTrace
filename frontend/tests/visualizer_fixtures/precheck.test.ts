import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import type { TraceDocument } from "../../../contracts/typescript/index.ts";
import { TraceCore } from "../../src/trace/index.ts";
import type { State, SelectedStepView } from "../../src/trace/index.ts";
import { ViewerController } from "../../src/viewer/controller.ts";
import { flush } from "../viewer/helpers.ts";
import { workerHarness } from "../performance/worker_helpers.ts";

interface EvidenceFixture {
  trace: TraceDocument;
  targetStep: number;
  currentState: State;
  predecessorState: State;
  expectedPositions: Record<string, number[]>;
}
function fixture(name: string): EvidenceFixture {
  return JSON.parse(
    readFileSync(new URL(`fixtures/${name}.json`, import.meta.url), "utf8"),
  ) as EvidenceFixture;
}
function select(f: EvidenceFixture): SelectedStepView {
  return TraceCore.load(f.trace).selectStep({
    step: f.targetStep,
    requestId: 1,
  });
}
/** Independent test oracle using the two decoded states, never a production adapter. */
function scalarPositions(f: EvidenceFixture): number[] {
  const before = f.predecessorState.objects.find((o) => o.id === "o1");
  const after = f.currentState.objects.find((o) => o.id === "o1");
  assert(before?.kind === "list" && after?.kind === "list");
  return after.items.flatMap((value, index) =>
    JSON.stringify(value) === JSON.stringify(before.items[index])
      ? []
      : [index],
  );
}

test("approved resolution supplies the exact scalar position without predecessor State", () => {
  const f = fixture("scalar"),
    view = select(f);
  assert.equal(view.step, 4);
  assert.deepEqual(view.state, f.currentState);
  assert.deepEqual(scalarPositions(f), [1]);
  assert.deepEqual(view.changedObjectIds, ["o1"]);
  assert.deepEqual(view.changedBindingNames, ["a"]);
  assert(!("previousState" in view));
  assert(!("changedArrayPositions" in view));
  assert.deepEqual(view.arrayPositionChanges, [
    { objectId: "o1", indices: [1] },
  ]);
});
test("different mutation histories differ only in arrayPositionChanges", () => {
  const a = fixture("history-a"),
    b = fixture("history-b");
  assert.notEqual(a.trace.sourceCode, b.trace.sourceCode);
  assert.deepEqual(scalarPositions(a), [1]);
  assert.deepEqual(scalarPositions(b), [0]);
  const { arrayPositionChanges: pa, ...oldA } = select(a),
    { arrayPositionChanges: pb, ...oldB } = select(b);
  assert.deepEqual(oldA, oldB); // State alone cannot distinguish the preceding mutation.
  assert.deepEqual(pa, [{ objectId: "o1", indices: [1] }]);
  assert.deepEqual(pb, [{ objectId: "o1", indices: [0] }]);
});
test("nested stable Ref carries enough descendant evidence for outer position zero", () => {
  const f = fixture("nested"),
    view = select(f);
  assert.deepEqual(view.state, f.currentState);
  assert.deepEqual(view.changedObjectIds, ["o1"]);
  assert.deepEqual(view.changedBindingNames, ["inner", "outer"]);
  const outer = view.state.objects.find((o) => o.id === "o2");
  assert(outer?.kind === "list");
  const positions = outer.items.flatMap((value, index) =>
    "$ref" in value && view.changedObjectIds.includes(value.$ref)
      ? [index]
      : [],
  );
  assert.deepEqual(positions, [0]);
  assert.deepEqual(f.expectedPositions["o2"], positions);
  assert.deepEqual(view.arrayPositionChanges, [
    { objectId: "o1", indices: [0] },
    { objectId: "o2", indices: [0] },
  ]);
});
test("direct target result is independent of previously selected UI Steps", () => {
  const f = fixture("scalar"),
    core = TraceCore.load(f.trace);
  const expected = select(f);
  for (const visited of [core.totalSteps, 1, f.targetStep - 1, 2]) {
    core.selectStep({ step: visited, requestId: 9 });
    assert.deepEqual(
      core.selectStep({ step: f.targetStep, requestId: 1 }),
      expected,
    );
  }
});
test("public readonly TRACE API exposes counts, not a predecessor/diff accessor", () => {
  const core = TraceCore.load(fixture("scalar").trace);
  assert.deepEqual(Object.keys(core.inspect()).sort(), [
    "checkpointCount",
    "eventCount",
    "materializedStateCount",
    "stateRecordCount",
  ]);
  assert.deepEqual(Object.getOwnPropertyNames(TraceCore.prototype).sort(), [
    "constructor",
    "inspect",
    "runId",
    "selectStep",
    "sourceCode",
    "totalSteps",
  ]);
});
test("VIEWER can jump directly to target without publishing its predecessor", async () => {
  const f = fixture("scalar"),
    viewer = new ViewerController();
  try {
    viewer.attach(TraceCore.load(f.trace));
    await flush();
    assert.equal(viewer.getSnapshot().committedStep, 1);
    assert(viewer.seek(f.targetStep));
    await flush();
    const view = viewer.getSnapshot().committedView!;
    assert.equal(view.step, f.targetStep);
    assert.deepEqual(view.state, f.currentState);
    assert.deepEqual(view.changedObjectIds, ["o1"]);
    assert(!("previousState" in view));
  } finally {
    viewer.dispose();
  }
});
test("actual isolated PERFORMANCE runtime preserves collision and single-fetch ownership", async () => {
  const h = await workerHarness(),
    views = new Map<string, SelectedStepView>();
  try {
    for (const name of ["scalar", "nested", "history-a", "history-b"]) {
      const f = fixture(name);
      h.setTrace(f.trace);
      const session = await h.loader.load(h.request(f.trace));
      const before = h.counts().gets;
      const request = { step: f.targetStep, requestId: 1 };
      const view = await session.executor.select(request);
      assert.deepEqual(view, select(f));
      assert(Object.isFrozen(view.state.objects));
      assert(Object.isFrozen(view.arrayPositionChanges));
      assert(
        view.arrayPositionChanges.every(
          (e) => Object.isFrozen(e) && Object.isFrozen(e.indices),
        ),
      );
      await session.executor.select({ step: 1, requestId: 2 });
      assert.deepEqual(await session.executor.select(request), view);
      assert.equal(h.counts().gets, before);
      views.set(name, view);
    }
    const { arrayPositionChanges: pa, ...oldA } = views.get("history-a")!,
      { arrayPositionChanges: pb, ...oldB } = views.get("history-b")!;
    assert.deepEqual(oldA, oldB);
    assert.notDeepEqual(pa, pb);
    assert.equal(h.counts().gets, 4);
    assert.equal(h.counts().peak, 1);
    assert(h.commands.every((c) => !("trace" in c) && !("body" in c)));
  } finally {
    await h.close();
  }
});
