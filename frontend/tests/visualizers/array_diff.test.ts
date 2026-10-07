import assert from "node:assert/strict";
import test from "node:test";
import { TraceCore } from "../../src/trace/index.ts";
import { fixture } from "../trace/helpers.ts";
import { int, float, bool, ref, list, binding, trace } from "./helpers.ts";
import type {
  Value,
  ObjectRecord,
} from "../../../contracts/typescript/index.ts";
const cases: readonly [string, Value[], Value[], number[]][] = [
  ["history A", [int(1), int(2)], [int(1), int(3)], [1]],
  ["history B", [int(9), int(3)], [int(1), int(3)], [0]],
  ["scalar unchanged", [int(1)], [int(1)], []],
  ["scalar replacement", [int(1)], [int(2)], [0]],
  ["bool/int", [bool(true)], [int(1)], [0]],
  ["float exact text", [float("1.0")], [float("1.0000000000000002")], [0]],
  ["negative zero exact", [float("0.0")], [float("-0.0")], [0]],
  ["append", [int(1)], [int(1), int(2)], [1]],
  ["insert", [int(1), int(2)], [int(0), int(1), int(2)], [0, 1, 2]],
  ["overwrite", [int(1), int(2)], [int(3), int(2)], [0]],
  ["pop tail", [int(1), int(2)], [int(1)], []],
  ["shorten", [int(1), int(2), int(3)], [], []],
  ["growth", [], [int(1), int(2)], [0, 1]],
  [
    "safe huge int exact",
    [int("999999999999999999999999")],
    [int("999999999999999999999998")],
    [0],
  ],
  [
    "199 included/200 excluded",
    Array.from({ length: 202 }, () => int(1)),
    Array.from({ length: 202 }, (_, i) => int(i >= 199 ? 2 : 1)),
    [199],
  ],
];
for (const [name, before, after, expected] of cases)
  test("array position diff: " + name, () => {
    const globals = [binding("a", ref("o1"))];
    const core = TraceCore.load(
      trace([
        { globals, objects: [list("o1", before)] },
        { globals, objects: [list("o1", after)] },
      ]),
    );
    for (const visited of [1, core.totalSteps, 2]) {
      core.selectStep({ step: visited, requestId: 0 });
      const v = core.selectStep({ step: 2, requestId: 1 });
      assert.deepEqual(
        v.arrayPositionChanges,
        expected.length ? [{ objectId: "o1", indices: expected }] : [],
      );
      assert(Object.isFrozen(v.arrayPositionChanges));
      if (expected.length)
        assert(Object.isFrozen(v.arrayPositionChanges[0]!.indices));
    }
  });
for (const scenario of ["nested", "alias", "self_cycle", "mutual_cycle"])
  test("real oracle list diff " + scenario, () => {
    const core = TraceCore.load(fixture(scenario + "-128").trace);
    let nonempty = false;
    for (let step = 1; step <= core.totalSteps; step++) {
      const v = core.selectStep({ step, requestId: step });
      for (const entry of v.arrayPositionChanges) {
        assert(entry.indices.every((i) => i >= 0 && i < 200));
        nonempty = true;
      }
    }
    assert(nonempty);
  });
for (const [name, before, after, expected] of [
  [
    "Ref replacement",
    [list("o1", [ref("o2")]), list("o2", [int(1)]), list("o3", [int(1)])],
    [list("o1", [ref("o3")]), list("o2", [int(1)]), list("o3", [int(1)])],
    ["o1"],
  ],
  [
    "same Ref descendant",
    [list("o1", [ref("o2")]), list("o2", [int(1)])],
    [list("o1", [ref("o2")]), list("o2", [int(2)])],
    ["o1", "o2"],
  ],
  [
    "self cycle",
    [list("o1", [ref("o1"), int(1)])],
    [list("o1", [ref("o1"), int(2)])],
    ["o1"],
  ],
  [
    "mutual cycle",
    [list("o1", [ref("o2")]), list("o2", [ref("o1"), int(1)])],
    [list("o1", [ref("o2")]), list("o2", [ref("o1"), int(2)])],
    ["o1", "o2"],
  ],
] as [string, ObjectRecord[], ObjectRecord[], string[]][])
  test("precise reachable diff " + name, () => {
    const globals = [binding("a", ref("o1"))],
      core = TraceCore.load(
        trace([
          { globals, objects: before },
          { globals, objects: after },
        ]),
      );
    const d = core.selectStep({ step: 2, requestId: 1 }).arrayPositionChanges;
    assert.deepEqual(
      d.map((e) => e.objectId),
      expected,
    );
    assert(d.every((e) => e.indices.includes(0)));
  });
test("new list, Step1, 200 bound and sorted IDs without Number", () => {
  const objects = [
      list(
        "o10",
        Array.from({ length: 201 }, () => int(1)),
      ),
      list("o2", [int(1)]),
    ],
    globals = [binding("a", ref("o10")), binding("b", ref("o2"))];
  const core = TraceCore.load(trace([{ objects, globals }])),
    v = core.selectStep({ step: 1, requestId: 1 });
  assert.deepEqual(
    v.arrayPositionChanges.map((e) => e.objectId),
    ["o2", "o10"],
  );
  assert.equal(v.arrayPositionChanges[1]!.indices.length, 200);
  const fresh = TraceCore.load(trace([{}, { objects, globals }])).selectStep({
    step: 2,
    requestId: 1,
  });
  assert.deepEqual(fresh.arrayPositionChanges, v.arrayPositionChanges);
});
test("removal and reappearance, aliases, no-change same version", () => {
  const objects = [list("o1", [int(1)])],
    globals = [binding("a", ref("o1")), binding("b", ref("o1"))];
  const t = trace([{ objects, globals }, {}, { objects, globals }]);
  const core = TraceCore.load(t);
  assert.deepEqual(
    core.selectStep({ step: 2, requestId: 1 }).arrayPositionChanges,
    [],
  );
  assert.deepEqual(
    core.selectStep({ step: 3, requestId: 1 }).arrayPositionChanges,
    [{ objectId: "o1", indices: [0] }],
  );
  const event = structuredClone(t.events[0]!);
  event.index = 1;
  t.events.splice(1, 0, event);
  t.events.forEach((e, i) => (e.index = i));
  assert.deepEqual(
    TraceCore.load(t).selectStep({ step: 2, requestId: 1 })
      .arrayPositionChanges,
    [],
  );
});
