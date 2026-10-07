import assert from "node:assert/strict";
import test from "node:test";
import { TraceCore } from "../../src/trace/index.ts";
import { fixture } from "./helpers.ts";

// Handwritten expectations. No production diff or Python diff generates these labels.
const bindingCases: readonly [
  number,
  string,
  readonly string[],
  readonly string[],
][] = [
  [1, "first appearance", ["x"], []],
  [2, "scalar reassignment", ["x"], []],
  [3, "add binding", ["y"], []],
  [4, "remove binding without fake row", [], []],
  [5, "new nested aliases", ["a", "b", "outer"], ["o1", "o2"]],
  [
    6,
    "nested list mutation affects both aliases and ancestor",
    ["a", "b", "outer"],
    ["o1"],
  ],
  [7, "mutual cycle mutation", ["a", "b", "outer"], ["o1"]],
  [8, "no-change event", [], []],
  [9, "object removal", [], ["o1", "o2"]],
  [10, "object reappearance", ["a", "b", "outer"], ["o1", "o2"]],
  [11, "RUN_END same version", [], []],
];
for (const interval of [1, 2, 128]) {
  for (const [step, name, names, objects] of bindingCases) {
    test(`changed indicators ${name}, interval ${interval}`, () => {
      const core = TraceCore.load(fixture(`binding_changes-${interval}`).trace);
      core.selectStep({ step: 10, requestId: 5 }); // Navigation history is irrelevant.
      const view = core.selectStep({ step, requestId: 6 });
      assert.deepEqual(view.changedBindingNames, names);
      assert.deepEqual(view.changedObjectIds, objects);
    });
  }
  for (const [step, label, expected] of [
    [1, "module globals", ["g"]],
    [2, "new function frame", ["x"]],
    [3, "local mutation", ["x"]],
    [4, "new recursive frame equal name/value", ["x"]],
    [5, "frame removal compares same surviving frame", []],
    [6, "back to module", []],
  ] as const) {
    test(`locals ${label}, interval ${interval}`, () => {
      const core = TraceCore.load(fixture(`frame_changes-${interval}`).trace);
      const view = core.selectStep({ step, requestId: 1 });
      assert.deepEqual(view.changedBindingNames, expected);
    });
  }
}
test("globals and selected locals form a deterministic name union", () => {
  const core = TraceCore.load(fixture("frame_changes-128").trace);
  const view = core.selectStep({
    step: 3,
    requestId: 10,
    requestedFrameId: "f1",
  });
  assert.deepEqual(view.changedBindingNames, []);
  assert.deepEqual(view.selectedLocals, view.globals);
});
test("canonical object equality ignores JSON member insertion order", () => {
  const input = fixture("binding_changes-128").trace;
  const delta = input.states[7]!;
  assert.equal(delta.kind, "delta");
  if (delta.kind !== "delta") throw new Error("fixture invariant");
  const previous = input.states[6]!;
  assert.equal(previous.kind, "delta");
  if (previous.kind !== "delta") throw new Error("fixture invariant");
  const prior = previous.changes.objectsUpsert.find((o) => o.id === "o1")!;
  // Version 6 is structurally allowed to carry a redundant full replacement.
  const record = JSON.parse(JSON.stringify(prior)) as typeof prior;
  delta.changes.objectsUpsert = [
    Object.fromEntries(Object.entries(record).reverse()) as typeof prior,
  ];
  const core = TraceCore.load(input);
  assert.deepEqual(
    core.selectStep({ step: 7, requestId: 1 }).changedObjectIds,
    [],
  );
});

test("binding scalar type change is distinct from equal-looking encoded value", () => {
  const input = fixture("binding_changes-128").trace;
  const r = input.states[2]!;
  if (r.kind !== "delta") throw new Error("fixture invariant");
  r.changes.globals = [
    { name: "x", value: { kind: "scalar", type: "str", value: "1" } },
  ];
  const core = TraceCore.load(input);
  assert.deepEqual(
    core.selectStep({ step: 2, requestId: 1 }).changedBindingNames,
    ["x"],
  );
});
test("binding rebind changes only that name even when both object records stay equal", () => {
  const input = fixture("binding_changes-128").trace;
  const r = input.states[6]!;
  if (r.kind !== "delta") throw new Error("fixture invariant");
  r.changes.objectsUpsert = [];
  r.changes.globals = [
    { name: "a", value: { $ref: "o1" } },
    { name: "b", value: { $ref: "o2" } },
    { name: "outer", value: { $ref: "o2" } },
    { name: "x", value: { kind: "scalar", type: "int", value: "2" } },
  ];
  const view = TraceCore.load(input).selectStep({ step: 6, requestId: 1 });
  assert.deepEqual(view.changedObjectIds, []);
  assert.deepEqual(view.changedBindingNames, ["b"]);
});
test("EXCEPTION metadata alone does not change variables or object records", () => {
  const input = fixture("handled-128").trace;
  const event = input.events.find((e) => e.kind === "EXCEPTION")!;
  assert.equal(event.stateVersion, input.events[event.index - 1]!.stateVersion);
  const view = TraceCore.load(input).selectStep({
    step: event.index + 1,
    requestId: 1,
  });
  assert.deepEqual(view.changedObjectIds, []);
  assert.deepEqual(view.changedBindingNames, []);
});
