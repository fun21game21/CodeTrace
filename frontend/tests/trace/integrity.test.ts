import assert from "node:assert/strict";
import test from "node:test";
import {
  TraceCore,
  TraceIntegrityError,
  TraceLoadError,
  StepOutOfRangeError,
} from "../../src/trace/index.ts";
import type { TraceDocument } from "../../../contracts/typescript/index.ts";
import { fixture, minimal } from "./helpers.ts";

type Damage = (trace: TraceDocument) => void;
const loadCases: readonly [string, Damage][] = [
  [
    "missing checkpoint zero",
    (d) => {
      d.states.shift();
    },
  ],
  [
    "duplicate version",
    (d) => {
      d.states[2]!.version = 1;
    },
  ],
  [
    "gap version",
    (d) => {
      d.states[2]!.version = 5;
    },
  ],
  [
    "wrong baseVersion",
    (d) => {
      const r = d.states[2]!;
      if (r.kind === "delta") r.baseVersion = 0;
    },
  ],
  [
    "unordered records",
    (d) => {
      [d.states[1], d.states[2]] = [d.states[2]!, d.states[1]!];
    },
  ],
  [
    "missing checkpoint target",
    (d) => {
      d.checkpointInterval = 2;
      d.limits.checkpoint_interval = 2;
    },
  ],
  [
    "mismatched interval",
    (d) => {
      d.checkpointInterval = 1;
    },
  ],
  [
    "zero interval",
    (d) => {
      d.checkpointInterval = 0;
    },
  ],
  [
    "unknown stateVersion",
    (d) => {
      d.events[1]!.stateVersion = 100;
    },
  ],
  [
    "decreasing stateVersion",
    (d) => {
      d.events[1]!.stateVersion = 0;
    },
  ],
  [
    "duplicate event index",
    (d) => {
      d.events[1]!.index = 0;
    },
  ],
  [
    "gap event index",
    (d) => {
      d.events[1]!.index = 5;
    },
  ],
  [
    "RUN_END missing",
    (d) => {
      d.events.pop();
    },
  ],
  [
    "two RUN_END",
    (d) => {
      d.events.push({ ...d.events.at(-1)!, index: 3 });
    },
  ],
  [
    "RUN_END not last",
    (d) => {
      [d.events[1], d.events[2]] = [
        { ...d.events[2]!, index: 1 },
        { ...d.events[1]!, index: 2 },
      ];
    },
  ],
  [
    "console overflow",
    (d) => {
      d.events[1]!.consoleOffset = 1;
    },
  ],
  [
    "console decreasing",
    (d) => {
      d.console.push({ index: 0, kind: "stdout", text: "a", requestId: null });
      d.events[0]!.consoleOffset = 1;
      d.events[2]!.consoleOffset = 1;
    },
  ],
  [
    "duplicate Console index",
    (d) => {
      d.console = [0, 0].map((index) => ({
        index,
        kind: "stdout",
        text: "a",
        requestId: null,
      }));
      d.events[2]!.consoleOffset = 2;
    },
  ],
  [
    "missing terminal console",
    (d) => {
      d.console.push({ index: 0, kind: "stdout", text: "a", requestId: null });
    },
  ],
  [
    "schema version",
    (d) => {
      Object.assign(d, { schemaVersion: "2.0" });
    },
  ],
  [
    "Python version",
    (d) => {
      Object.assign(d, { pythonVersion: "3.14.7" });
    },
  ],
  [
    "nonempty zero checkpoint",
    (d) => {
      const r = d.states[0]!;
      if (r.kind === "checkpoint")
        r.state.globals = [
          { name: "x", value: { kind: "scalar", type: "int", value: "1" } },
        ];
    },
  ],
  [
    "terminal frame/source",
    (d) => {
      Object.assign(d.events[2]!, {
        frameId: "f1",
        source: { fileName: "<codetrace-user>", lineNumber: 1 },
      });
    },
  ],
  [
    "upsert/remove intersection",
    (d) => {
      const r = d.states[1]!;
      if (r.kind === "delta") r.changes.framesRemove = ["f1"];
    },
  ],
  [
    "duplicate upsert",
    (d) => {
      const r = d.states[1]!;
      if (r.kind === "delta")
        r.changes.framesUpsert.push(r.changes.framesUpsert[0]!);
    },
  ],
];
for (const [name, damage] of loadCases)
  test(`load corruption: ${name}`, () => {
    const input = minimal();
    damage(input);
    assert.throws(() => TraceCore.load(input), TraceLoadError);
  });
for (const [name, damage, step] of [
  [
    "dangling global Ref",
    (d: TraceDocument) => {
      const r = d.states[1]!;
      if (r.kind === "delta")
        r.changes.globals = [{ name: "a", value: { $ref: "o999" } }];
    },
    1,
  ],
  [
    "missing stack frame",
    (d: TraceDocument) => {
      const r = d.states[1]!;
      if (r.kind === "delta") r.changes.stack = ["f999"];
    },
    1,
  ],
  [
    "cyclic parent",
    (d: TraceDocument) => {
      const r = d.states[1]!;
      if (r.kind === "delta") r.changes.framesUpsert[0]!.parentId = "f1";
    },
    1,
  ],
  [
    "remove unknown frame",
    (d: TraceDocument) => {
      const r = d.states[2]!;
      if (r.kind === "delta") r.changes.framesRemove = ["f999"];
    },
    3,
  ],
  [
    "remove unknown object",
    (d: TraceDocument) => {
      const r = d.states[2]!;
      if (r.kind === "delta") r.changes.objectsRemove = ["o999"];
    },
    3,
  ],
  [
    "event missing active frame",
    (d: TraceDocument) => {
      const e = d.events[0]!;
      if (e.kind !== "RUN_END") e.frameId = "f999";
    },
    1,
  ],
] as const)
  test(`seek corruption: ${name}`, () => {
    const input = minimal();
    damage(input);
    const core = TraceCore.load(input);
    assert.throws(
      () => core.selectStep({ step, requestId: 1 }),
      TraceIntegrityError,
    );
  });
test("damaged object upsert is checked after whole delta, prior view remains valid", () => {
  const input = fixture("binding_changes-128").trace;
  const r = input.states[6]!;
  if (r.kind !== "delta") throw new Error("fixture invariant");
  const o = r.changes.objectsUpsert[0]!;
  if (o.kind !== "list") throw new Error("fixture invariant");
  o.items = [{ $ref: "o999" }];
  const core = TraceCore.load(input);
  const old = core.selectStep({ step: 5, requestId: 1 });
  const preserved = JSON.stringify(old);
  assert.throws(
    () => core.selectStep({ step: 6, requestId: 2 }),
    TraceIntegrityError,
  );
  assert.equal(JSON.stringify(old), preserved);
  assert.deepEqual(core.selectStep({ step: 5, requestId: 1 }), old);
});
test("returnValue requires retained eventRoot", () => {
  const input = fixture("return_root-128").trace;
  const index = input.events.findIndex(
    (e) => e.kind === "RETURN" && e.payload.returnValueKnown,
  );
  const version = input.events[index]!.stateVersion;
  const r = input.states[version]!;
  if (r.kind !== "delta") throw new Error("fixture invariant");
  r.changes.eventRoots = [];
  const core = TraceCore.load(input);
  assert.throws(
    () => core.selectStep({ step: index + 1, requestId: 1 }),
    TraceIntegrityError,
  );
});
for (const step of [0, -1, 4, NaN, Infinity, 1.5, Number.MAX_SAFE_INTEGER + 1])
  test(`Step rejects ${step}`, () =>
    assert.throws(
      () => TraceCore.load(minimal()).selectStep({ step, requestId: 1 }),
      StepOutOfRangeError,
    ));
for (const requestId of [-1, NaN, 0.5, Infinity])
  test(`requestId rejects ${requestId}`, () =>
    assert.throws(
      () => TraceCore.load(minimal()).selectStep({ step: 1, requestId }),
      RangeError,
    ));
for (const [name, input] of [
  ["undefined", undefined],
  ["NaN", NaN],
  ["bigint", 1n],
  ["function", () => 1],
  ["Date", new Date()],
  ["symbol key", { [Symbol()]: 1 }],
  ["sparse array", new Array(3)],
  ["nonenumerable", Object.defineProperty({}, "hidden", { value: 1 })],
] as const)
  test(`safe JSON guard: ${name}`, () =>
    assert.throws(() => TraceCore.load(input), TraceLoadError));
test("getter is rejected without invocation, even before CONTRACTS validation", () => {
  const input = minimal();
  let invoked = 0;
  Object.defineProperty(input, "sourceCode", {
    get() {
      invoked++;
      throw new Error("executed");
    },
    enumerable: true,
  });
  assert.throws(() => TraceCore.load(input), TraceLoadError);
  assert.equal(invoked, 0);
});
test("live cyclic JSON is rejected", () => {
  const input: unknown[] = [];
  input.push(input);
  assert.throws(() => TraceCore.load(input), TraceLoadError);
});
test("load does not mutate, reorder or freeze caller-owned DTOs", () => {
  const input = fixture("alias-128").trace;
  const original = structuredClone(input);
  const core = TraceCore.load(input);
  core.selectStep({ step: core.totalSteps, requestId: 1 });
  assert.deepEqual(input, original);
  assert.equal(Object.isFrozen(input), false);
  input.sourceCode = "corrupted externally";
  input.events.length = 0;
  input.states.length = 0;
  assert.equal(core.sourceCode, original.sourceCode);
  assert.equal(core.totalSteps, original.events.length);
  assert.doesNotThrow(() =>
    core.selectStep({ step: core.totalSteps, requestId: 2 }),
  );
});
