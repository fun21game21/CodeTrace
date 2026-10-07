import assert from "node:assert/strict";
import test from "node:test";
import { TraceCore } from "../../src/trace/index.ts";
import { fixture, frozen, minimal } from "./helpers.ts";

for (const [label, requestedFrameId, expected] of [
  ["default top", undefined, "f3"],
  ["existing bottom", "f1", "f1"],
  ["existing caller", "f2", "f2"],
  ["explicit globals", null, null],
  ["missing requested frame", "f999", "f3"],
] as const)
  test(`selected scope: ${label}`, () => {
    const core = TraceCore.load(fixture("frame_changes-128").trace);
    const request = {
      step: 4,
      requestId: 80,
      ...(requestedFrameId === undefined ? {} : { requestedFrameId }),
    };
    const view = core.selectStep(request);
    assert.equal(view.selectedFrameId, expected);
    assert.deepEqual(
      view.callStack.map((f) => f.id),
      ["f1", "f2", "f3"],
    );
    if (expected === null) assert.deepEqual(view.selectedLocals, []);
    else if (expected === "f1") {
      assert.equal(view.selectedLocals, view.globals);
      assert.deepEqual(view.callStack[0]!.locals, []);
    } else
      assert.deepEqual(
        view.selectedLocals,
        view.state.frames.find((f) => f.id === expected)!.locals,
      );
    frozen(view);
  });
test("selected recursive frame disappears and falls back to surviving top", () => {
  const core = TraceCore.load(fixture("frame_changes-128").trace);
  assert.equal(
    core.selectStep({ step: 5, requestId: 1, requestedFrameId: "f3" })
      .selectedFrameId,
    "f2",
  );
});
test("empty stack exposes globals with no invented frame", () => {
  const core = TraceCore.load(fixture("assignment-128").trace);
  const view = core.selectStep({
    step: core.totalSteps,
    requestId: 0,
    requestedFrameId: "f1",
  });
  assert.equal(view.selectedFrameId, null);
  assert.deepEqual(view.selectedLocals, []);
  assert.deepEqual(view.callStack, []);
  assert.ok(view.globals.length > 0);
});
for (const name of ["function", "recursion", "class", "return_root"])
  test(`real TRACING stack/return semantics: ${name}`, () => {
    const input = fixture(`${name}-128`);
    const core = TraceCore.load(input.trace);
    for (const event of input.trace.events) {
      const view = core.selectStep({ step: event.index + 1, requestId: 1 });
      if (event.kind === "RETURN")
        assert.equal(view.callStack.at(-1)?.id, event.frameId);
      if (event.kind === "RETURN" && event.payload.returnValueKnown)
        assert.ok(
          view.state.eventRoots.some(
            (value) =>
              JSON.stringify(value) ===
              JSON.stringify(event.payload.returnValue),
          ),
        );
    }
    const final = core.selectStep({ step: core.totalSteps, requestId: 2 });
    assert.deepEqual(final.state.stack, []);
    assert.deepEqual(final.state.eventRoots, []);
  });
for (const name of ["console", "input"])
  test(`Console prefix: ${name}, no future entries and full RUN_END`, () => {
    const input = fixture(`${name}-128`);
    const core = TraceCore.load(input.trace);
    for (const event of input.trace.events) {
      const view = core.selectStep({ step: event.index + 1, requestId: 1 });
      assert.deepEqual(
        view.consoleEntries,
        input.trace.console.slice(0, event.consoleOffset),
      );
      assert.equal("console" in view.state, false);
    }
    assert.deepEqual(
      core.selectStep({ step: core.totalSteps, requestId: 2 }).consoleEntries,
      input.trace.console,
    );
    assert.deepEqual(
      core.selectStep({ step: 1, requestId: 3 }).consoleEntries,
      [],
    );
    if (name === "input") {
      assert.ok(
        input.trace.console.some(
          (e) => e.kind === "input" && e.text === "  😀  \n",
        ),
      );
      assert.ok(
        input.trace.console.some((e) => e.kind === "input" && e.text === "\n"),
      );
      assert.ok(input.trace.console.some((e) => e.text === "Q:"));
    } else {
      assert.ok(input.trace.console.some((e) => e.kind === "stderr"));
      assert.ok(input.trace.console.some((e) => e.text === "A"));
    }
  });
for (const name of ["assignment", "syntax", "propagated", "handled", "partial"])
  test(`highlight/diagnostic/completion: ${name}`, () => {
    const input = fixture(`${name}-128`);
    const core = TraceCore.load(input.trace);
    for (const event of input.trace.events) {
      const view = core.selectStep({ step: event.index + 1, requestId: 1 });
      if (event.kind !== "RUN_END") assert.equal(view.completion, null);
      if (event.kind === "LINE")
        assert.deepEqual(view.highlight, {
          lineNumber: event.source.lineNumber,
          mode: "before_line",
        });
      if (event.kind === "EXCEPTION")
        assert.deepEqual(view.eventDiagnostic, event.payload.diagnostic);
      else assert.equal(view.eventDiagnostic, null);
    }
    const end = core.selectStep({ step: core.totalSteps, requestId: 2 });
    assert.deepEqual(end.completion, input.trace.completion);
    assert.equal(end.state.version, input.trace.events.at(-1)!.stateVersion);
    if (name === "assignment")
      assert.deepEqual(end.highlight, { lineNumber: null, mode: "none" });
    if (name === "syntax") {
      assert.equal(end.completion!.stateAccuracy, "unavailable");
      assert.equal(end.highlight.mode, "error");
      assert.equal(end.state.version, 0);
    }
    if (name === "partial")
      assert.equal(end.completion!.stateAccuracy, "last_observed");
  });
test("null CALL/RETURN line never invents line 1", () => {
  const core = TraceCore.load(minimal());
  assert.deepEqual(core.selectStep({ step: 1, requestId: 1 }).highlight, {
    lineNumber: null,
    mode: "none",
  });
  assert.deepEqual(core.selectStep({ step: 2, requestId: 1 }).highlight, {
    lineNumber: null,
    mode: "none",
  });
});
test("prototype names, Unicode, huge int and special float strings stay exact", () => {
  const input = fixture("specials-128");
  const prototype = Object.getPrototypeOf({}) as unknown;
  const core = TraceCore.load(input.trace);
  const view = core.selectStep({ step: core.totalSteps, requestId: 1 });
  const bindings = new Map(view.globals.map((b) => [b.name, b.value]));
  for (const name of ["__proto__", "constructor", "prototype", "Я"])
    assert.ok(bindings.has(name));
  for (const [name, value] of [
    ["big", "123456789012345678901234567890"],
    ["neg", "-0.0"],
    ["nan", "nan"],
    ["positive", "inf"],
    ["negative", "-inf"],
  ])
    assert.equal((bindings.get(name!) as { value: unknown }).value, value);
  assert.equal(Object.getPrototypeOf({}), prototype);
  assert.deepEqual(view.globals, input.expected.at(-1)!.state.globals);
});
test("all object variants remain encoded; dict int/string keys stay distinct", () => {
  const input = fixture("all_types-128");
  const core = TraceCore.load(input.trace);
  const view = core.selectStep({ step: core.totalSteps, requestId: 1 });
  for (const kind of ["tuple", "dict", "set", "deque", "opaque"])
    assert.ok(
      view.state.objects.some((o) => o.kind === kind),
      kind,
    );
  const dict = view.state.objects.find((o) => o.kind === "dict")!;
  if (dict.kind !== "dict") throw new Error("fixture invariant");
  assert.deepEqual(
    dict.entries.map((e) => e.key),
    [
      { kind: "scalar", type: "int", value: "1" },
      { kind: "scalar", type: "str", value: "1" },
    ],
  );
  const deque = view.state.objects.find((o) => o.kind === "deque")!;
  if (deque.kind !== "deque") throw new Error("fixture invariant");
  assert.equal(deque.maxlen, 3);
  assert.deepEqual(
    deque.items.map((v) => ("value" in v ? v.value : v.$ref)),
    ["2", "3", "4"],
  );
});
test("instance attributes and alias refs remain DTOs, never live instances", () => {
  const core = TraceCore.load(fixture("class-128").trace);
  const view = core.selectStep({ step: core.totalSteps, requestId: 1 });
  assert.ok(view.state.objects.some((o) => o.kind === "instance"));
  frozen(view);
});
for (const name of ["alias", "self_cycle", "mutual_cycle"])
  test(`identity and encoded cycle preserved: ${name}`, () => {
    const input = fixture(`${name}-128`);
    const core = TraceCore.load(input.trace);
    const view = core.selectStep({ step: core.totalSteps, requestId: 1 });
    assert.deepEqual(view.state, input.expected.at(-1)!.state);
    if (name === "alias")
      assert.deepEqual(view.globals[0]!.value, view.globals[1]!.value);
    assert.doesNotThrow(() => JSON.stringify(view));
  });
test("numeric ObjectId ordering never rounds huge identity suffixes", () => {
  const input = minimal();
  const ids = ["o2", "o10", "o9007199254740993", "o9007199254740994"];
  const r = input.states[1]!;
  if (r.kind !== "delta") throw new Error("fixture invariant");
  r.changes.globals = [{ name: "x", value: { $ref: "o2" } }];
  r.changes.objectsUpsert = [...ids].reverse().map((id) => ({
    id,
    kind: "list",
    items: ids.filter((v) => v !== id).map(($ref) => ({ $ref })),
  }));
  const core = TraceCore.load(input);
  const view = core.selectStep({ step: 1, requestId: 1 });
  assert.deepEqual(
    view.state.objects.map((o) => o.id),
    ids,
  );
  assert.deepEqual(view.changedObjectIds, ids);
});
test("atomic replacement accepts parent Ref before child upsert", () => {
  const input = fixture("binding_changes-128").trace;
  const r = input.states[5]!;
  if (r.kind !== "delta") throw new Error("fixture invariant");
  r.changes.objectsUpsert.reverse(); // o2 references o1, which is inserted later.
  const core = TraceCore.load(input);
  assert.deepEqual(
    core.selectStep({ step: 5, requestId: 1 }).state.objects.map((o) => o.id),
    ["o1", "o2"],
  );
});
test("100k events: compact indexes and at most two cached materialized states", () => {
  const input = minimal();
  input.sourceCode = "pass";
  const event = {
    ...input.events[0]!,
    kind: "LINE" as const,
    frameId: "f1",
    source: { fileName: "<codetrace-user>" as const, lineNumber: 1 },
    payload: {},
  };
  input.events = Array.from({ length: 99999 }, (_, index) => ({
    ...event,
    index,
  }));
  input.events.push({
    index: 99999,
    kind: "RUN_END",
    frameId: null,
    source: null,
    payload: {},
    stateVersion: 2,
    consoleOffset: 0,
  });
  const core = TraceCore.load(input);
  assert.deepEqual(core.inspect(), {
    eventCount: 100000,
    stateRecordCount: 3,
    checkpointCount: 1,
    materializedStateCount: 0,
  });
  for (const step of [5, 105, 2, 900, 128, 127, 100000, 1, 99999, 50000]) {
    const view = core.selectStep({ step, requestId: step });
    assert.equal(view.event.index, step - 1);
    assert.equal(view.state.version, step === 100000 ? 2 : 1);
    assert.ok(core.inspect().materializedStateCount <= 2);
  }
  assert.equal(core.inspect().stateRecordCount, 3);
});

test("1000-object cycle validates and propagates a descendant change iteratively", () => {
  const input = minimal();
  input.sourceCode = "pass";
  const first = input.states[1]!;
  const final = input.states[2]!;
  if (first.kind !== "delta" || final.kind !== "delta")
    throw new Error("fixture invariant");
  first.changes.globals = [{ name: "x", value: { $ref: "o1" } }];
  first.changes.objectsUpsert = Array.from({ length: 1000 }, (_, index) => ({
    id: `o${index + 1}`,
    kind: "list",
    items: [{ $ref: `o${index === 999 ? 1 : index + 2}` }],
  }));
  const changed = structuredClone(final);
  changed.changes.framesRemove = [];
  changed.changes.stack = null;
  changed.changes.objectsUpsert = [
    {
      id: "o1000",
      kind: "list",
      items: [{ $ref: "o1" }, { kind: "scalar", type: "int", value: "1" }],
    },
  ];
  final.version = 3;
  final.baseVersion = 2;
  input.states = [input.states[0]!, first, changed, final];
  input.events[1]!.stateVersion = 2;
  input.events[2]!.stateVersion = 3;
  const core = TraceCore.load(input);
  const view = core.selectStep({ step: 2, requestId: 1 });
  assert.deepEqual(view.changedObjectIds, ["o1000"]);
  assert.deepEqual(view.changedBindingNames, ["x"]);
  assert.equal(view.state.objects.length, 1000);
});
