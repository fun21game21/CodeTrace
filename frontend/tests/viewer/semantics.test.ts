import assert from "node:assert/strict";
import test from "node:test";
import { TraceCore } from "../../src/trace/index.ts";
import { ViewerController } from "../../src/viewer/controller.ts";
import { lineDecorations } from "../../src/editor/highlight.ts";
import { fixture, manifest, shuffled } from "../trace/helpers.ts";
import { flush } from "./helpers.ts";

for (const entry of manifest)
  test(
    "real TRACE oracle through Viewer every Step: " + entry.file,
    async () => {
      const input = fixture(entry.file.replace(".json", "")),
        core = TraceCore.load(input.trace);
      const viewer = new ViewerController();
      viewer.attach(core);
      await flush();
      const order = [
        ...Array.from({ length: core.totalSteps }, (_, i) => i + 1),
        ...Array.from(
          { length: core.totalSteps },
          (_, i) => core.totalSteps - i,
        ),
        ...shuffled(core.totalSteps),
      ];
      for (const step of order) {
        // Explicit top selection makes the independent oracle's default scope comparable.
        viewer.seek(step);
        await flush();
        viewer.selectFrame("f999999");
        await flush();
        const s = viewer.getSnapshot(),
          v = s.committedView!;
        assert.equal(v.step, step);
        assert.equal(v.requestId, s.latestRequestId);
        assert.equal(s.committedStep, step);
        for (const key of Object.keys(
          input.expected[step - 1]!,
        ) as (keyof (typeof input.expected)[number])[])
          assert.deepEqual(
            v[key],
            input.expected[step - 1]![key],
            entry.file + " step " + step + " " + key,
          );
        assert.equal(Object.isFrozen(v), true);
      }
      viewer.dispose();
    },
  );
test("changed indicators deterministic after random and reverse navigation", async () => {
  const core = TraceCore.load(fixture("binding_changes-128").trace),
    viewer = new ViewerController();
  viewer.attach(core);
  await flush();
  const baseline = new Map<number, readonly string[]>();
  for (const step of [1, 2, 3, 4, 5, 6]) {
    viewer.seek(step);
    await flush();
    baseline.set(step, viewer.getSnapshot().committedView!.changedBindingNames);
  }
  for (const step of [5, 2, 6, 1, 3, 4]) {
    viewer.seek(step);
    await flush();
    assert.deepEqual(
      viewer.getSnapshot().committedView!.changedBindingNames,
      baseline.get(step),
    );
  }
  viewer.dispose();
});
for (const scenario of ["alias", "nested"])
  test("mutable descendant marks all aliases: " + scenario, async () => {
    const core = TraceCore.load(fixture(scenario + "-128").trace),
      viewer = new ViewerController();
    viewer.attach(core);
    await flush();
    let observed = false;
    for (let step = 1; step <= core.totalSteps; step++) {
      viewer.seek(step);
      await flush();
      const v = viewer.getSnapshot().committedView!;
      if (
        v.changedObjectIds.length &&
        v.globals.length >= 2 &&
        v.globals[0]!.value &&
        "$ref" in v.globals[0]!.value
      ) {
        const direct = core.selectStep({
          step,
          requestId: v.requestId,
          requestedFrameId: v.selectedFrameId,
        });
        assert.deepEqual(v.changedBindingNames, direct.changedBindingNames);
        if (scenario === "alias" && v.changedBindingNames.includes("a")) {
          assert(v.changedBindingNames.includes("b"));
          observed = true;
        }
        if (scenario === "nested" && v.changedBindingNames.length)
          observed = true;
      }
    }
    assert(observed);
    viewer.dispose();
  });
test("no-change Event never gains a navigation-based changed mark", async () => {
  const core = TraceCore.load(fixture("unchanged-128").trace),
    viewer = new ViewerController();
  viewer.attach(core);
  await flush();
  const step =
    fixture("unchanged-128").trace.events.findIndex(
      (e, i, all) => i > 0 && e.stateVersion === all[i - 1]!.stateVersion,
    ) + 1;
  assert(step > 1);
  viewer.seek(step);
  await flush();
  assert.deepEqual(viewer.getSnapshot().committedView!.changedBindingNames, []);
  viewer.seek(core.totalSteps);
  await flush();
  viewer.seek(step);
  await flush();
  assert.deepEqual(viewer.getSnapshot().committedView!.changedBindingNames, []);
  viewer.dispose();
});
for (const scenario of [
  "assignment",
  "function",
  "recursion",
  "handled",
  "propagated",
  "syntax",
  "partial",
])
  test("event/RETURN/highlight/diagnostic semantics: " + scenario, async () => {
    const input = fixture(scenario + "-128"),
      core = TraceCore.load(input.trace),
      viewer = new ViewerController();
    viewer.attach(core);
    await flush();
    for (let step = 1; step <= core.totalSteps; step++) {
      viewer.seek(step);
      await flush();
      const v = viewer.getSnapshot().committedView!;
      assert.deepEqual(v.highlight, input.expected[step - 1]!.highlight);
      assert.deepEqual(
        v.eventDiagnostic,
        input.expected[step - 1]!.eventDiagnostic,
      );
      assert.deepEqual(v.completion, input.expected[step - 1]!.completion);
      if (v.event.kind === "RETURN")
        assert.equal(v.callStack.at(-1)!.id, v.event.frameId);
      if (v.event.kind !== "RUN_END") assert.equal(v.completion, null);
    }
    viewer.dispose();
  });
for (const scenario of ["console", "input"])
  test("Console exact prefix forwards/backwards: " + scenario, async () => {
    const input = fixture(scenario + "-128"),
      core = TraceCore.load(input.trace),
      viewer = new ViewerController();
    viewer.attach(core);
    await flush();
    for (const step of [...shuffled(core.totalSteps), core.totalSteps, 1]) {
      viewer.seek(step);
      await flush();
      const v = viewer.getSnapshot().committedView!;
      assert.deepEqual(
        v.consoleEntries,
        input.trace.console.slice(
          0,
          input.trace.events[step - 1]!.consoleOffset,
        ),
      );
      if (step === 1) assert.equal(v.consoleEntries.length, 0);
      if (step === core.totalSteps)
        assert.deepEqual(v.consoleEntries, input.trace.console);
    }
    viewer.dispose();
  });
test("Monaco plan absent before selection and at null/errorless RUN_END", async () => {
  assert.deepEqual(lineDecorations(null), []);
  assert.deepEqual(lineDecorations({ lineNumber: null, mode: "event" }), []);
  const core = TraceCore.load(fixture("assignment-128").trace),
    viewer = new ViewerController();
  viewer.attach(core);
  await flush();
  viewer.seek(core.totalSteps);
  await flush();
  assert.deepEqual(
    lineDecorations(viewer.getSnapshot().committedView!.highlight),
    [],
  );
  assert.equal(core.sourceCode, fixture("assignment-128").trace.sourceCode);
  viewer.dispose();
});
for (const mode of ["before_line", "event", "error"] as const)
  test("Monaco whole-line decoration " + mode, () => {
    const plan = lineDecorations({ lineNumber: 7, mode });
    assert.equal(plan[0]!.range.startLineNumber, 7);
    assert.equal(plan[0]!.range.endLineNumber, 7);
    assert.equal(plan[0]!.options.isWholeLine, true);
    assert.equal(plan[0]!.options.className, "ct-line-" + mode);
  });
