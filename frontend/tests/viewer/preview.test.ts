import assert from "node:assert/strict";
import test from "node:test";
import { previewValue } from "../../src/viewer/value_preview.ts";
import type { StoredObject, StoredValue } from "../../src/trace/types.ts";
import { fixture } from "../trace/helpers.ts";
import { TraceCore } from "../../src/trace/index.ts";
const table = new Map<string, StoredObject>();
for (const [type, value, expected] of [
  ["none", null, "None"],
  ["bool", true, "True"],
  ["bool", false, "False"],
  ["int", "123456789012345678901234567890", "123456789012345678901234567890"],
  ["int", "-9007199254740993", "-9007199254740993"],
  ["float", "-0.0", "-0.0"],
  ["float", "nan", "nan"],
  ["float", "inf", "inf"],
  ["float", "-inf", "-inf"],
  ["str", "Мир 🙂", '"Мир 🙂"'],
  ["str", 'a"b\n\t\\', '"a\\"b\\n\\t\\\\"'],
  ["str", "<script>x</script>", '"<script>x</script>"'],
] as const)
  test("encoded preview " + type + " " + String(value), () => {
    assert.equal(
      previewValue({ kind: "scalar", type, value } as StoredValue, table),
      expected,
    );
  });
for (const scenario of [
  "list",
  "alias",
  "self_cycle",
  "mutual_cycle",
  "all_types",
  "class",
  "specials",
])
  test("real object graph bounded identity preview: " + scenario, () => {
    const core = TraceCore.load(fixture(scenario + "-128").trace),
      v = core.selectStep({ step: core.totalSteps, requestId: 1 });
    const objects = new Map(v.state.objects.map((o) => [o.id, o]));
    for (const b of v.globals) {
      const text = previewValue(b.value, objects);
      if ("$ref" in b.value) {
        assert(text.includes(b.value.$ref));
        assert(text.includes(" · "));
      }
    }
    if (scenario === "self_cycle" || scenario === "mutual_cycle")
      assert(
        v.globals.some((b) => previewValue(b.value, objects).includes("cycle")),
      );
    if (scenario === "alias")
      assert.equal(
        previewValue(v.globals[0]!.value, objects),
        previewValue(v.globals[1]!.value, objects),
      );
  });
test("depth truncation explicit and identity retained", () => {
  const objects = new Map<string, StoredObject>([
    ["o1", { id: "o1", kind: "list", items: [{ $ref: "o2" }] }],
    ["o2", { id: "o2", kind: "tuple", items: [] }],
  ]);
  assert.equal(
    previewValue({ $ref: "o1" }, objects, { maxDepth: 1 }),
    "o1 · list [o2 · tuple … depth limit]",
  );
});
test("item truncation explicitly counts omitted entries", () => {
  const objects = new Map<string, StoredObject>([
    [
      "o1",
      {
        id: "o1",
        kind: "list",
        items: Array.from({ length: 8 }, () => ({
          kind: "scalar",
          type: "int",
          value: "1",
        })),
      },
    ],
  ]);
  assert(
    previewValue({ $ref: "o1" }, objects, { maxItems: 2 }).includes(
      "… 6 hidden",
    ),
  );
});
test("node budget is bounded across branches", () => {
  const objects = new Map<string, StoredObject>([
    ["o1", { id: "o1", kind: "list", items: [{ $ref: "o2" }, { $ref: "o3" }] }],
    ["o2", { id: "o2", kind: "list", items: [] }],
    ["o3", { id: "o3", kind: "list", items: [] }],
  ]);
  assert(
    previewValue({ $ref: "o1" }, objects, { maxNodes: 1 }).includes(
      "preview limit",
    ),
  );
});
test("opaque preview uses encoded metadata only", () => {
  const objects = new Map<string, StoredObject>([
    [
      "o7",
      {
        id: "o7",
        kind: "opaque",
        pythonType: "function",
        reason: "unsupported_type",
      },
    ],
  ]);
  assert.equal(
    previewValue({ $ref: "o7" }, objects),
    "o7 · opaque (function; unsupported_type)",
  );
});
test("Python dict int and string keys retain separate quoting", () => {
  const objects = new Map<string, StoredObject>([
    [
      "o1",
      {
        id: "o1",
        kind: "dict",
        entries: [
          {
            key: { kind: "scalar", type: "int", value: "1" },
            value: { kind: "scalar", type: "bool", value: true },
          },
          {
            key: { kind: "scalar", type: "str", value: "1" },
            value: { kind: "scalar", type: "bool", value: false },
          },
        ],
      },
    ],
  ]);
  assert.equal(
    previewValue({ $ref: "o1" }, objects),
    'o1 · dict [1: True, "1": False]',
  );
});
test("missing identity is explicit and does not recurse", () => {
  assert.equal(previewValue({ $ref: "o999" }, table), "o999 · unavailable");
});
test("preview limits reject invalid values", () => {
  assert.throws(
    () => previewValue({ $ref: "o1" }, table, { maxDepth: -1 }),
    RangeError,
  );
});
