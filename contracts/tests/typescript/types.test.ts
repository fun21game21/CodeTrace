/** These literals keep erased TypeScript DTOs in the shared-fixture checks. */
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import type {
  Value,
  ObjectRecord,
  Frame,
  NormalizedState,
  StateDelta,
  StateRecord,
  ConsoleEntry,
  Completion,
  TraceEvent,
  TransportTraceRecord,
} from "../../typescript/types.ts";
import { validateContract } from "../../typescript/validators.ts";

const none: Value = { kind: "scalar", type: "none", value: null };
const integer: Value = {
  kind: "scalar",
  type: "int",
  value: "9007199254740993",
};
const reference: Value = { $ref: "o1" };
const values: Value[] = [
  none,
  { kind: "scalar", type: "bool", value: true },
  integer,
  { kind: "scalar", type: "float", value: "nan" },
  { kind: "scalar", type: "str", value: "x" },
  reference,
];
const objects: ObjectRecord[] = [
  { id: "o1", kind: "list", items: [reference] },
  { id: "o1", kind: "tuple", items: [none] },
  { id: "o1", kind: "dict", entries: [{ key: integer, value: none }] },
  { id: "o1", kind: "set", items: [integer] },
  { id: "o1", kind: "deque", items: [], maxlen: null },
  {
    id: "o1",
    kind: "instance",
    className: "Node",
    attrs: [{ name: "next", value: reference }],
  },
  { id: "o1", kind: "opaque", pythonType: "bytes", reason: "unsupported_type" },
];
const frame: Frame = {
  id: "f1",
  parentId: null,
  kind: "module",
  name: "<module>",
  qualifiedName: "<module>",
  definitionLine: null,
  callSite: null,
  parameterNames: [],
  localsSource: "globals",
  locals: [],
};
const state: NormalizedState = {
  version: 1,
  globals: [{ name: "a", value: reference }],
  frames: [frame],
  stack: ["f1"],
  objects: [objects[0]!],
  eventRoots: [],
};
const changes: StateDelta = {
  globals: null,
  stack: [],
  eventRoots: null,
  framesUpsert: [],
  framesRemove: ["f1"],
  objectsUpsert: [],
  objectsRemove: [],
};
const records: StateRecord[] = [
  { kind: "checkpoint", version: 1, state },
  { kind: "delta", version: 2, baseVersion: 1, changes },
];
const entry: ConsoleEntry = {
  index: 0,
  kind: "stdout",
  text: "hello\n",
  requestId: null,
};
const completion: Completion = {
  outcome: "SUCCESS",
  reason: "NONE",
  traceComplete: true,
  stateAccuracy: "final",
  primaryDiagnostic: null,
  acceptedAt: "2026-10-05T15:00:00.000Z",
  finishedAt: "2026-10-05T15:00:00.125Z",
  activeElapsedMs: 125.25,
  waitingElapsedMs: 0,
  exitCode: 0,
  cleanupConfirmed: true,
};
const end: TraceEvent = {
  index: 0,
  kind: "RUN_END",
  stateVersion: 1,
  frameId: null,
  source: null,
  consoleOffset: 0,
  payload: {},
};
const transport: TransportTraceRecord = { kind: "state", record: records[0]! };
const cases: [string, unknown][] = [
  ...values.map((v) => ["Value", v] as [string, unknown]),
  ...objects.map((v) => ["ObjectRecord", v] as [string, unknown]),
  ["Frame", frame],
  ["NormalizedState", state],
  ["StateDelta", changes],
  ...records.map((v) => ["StateRecord", v] as [string, unknown]),
  ["ConsoleEntry", entry],
  ["Completion", completion],
  ["TraceEvent", end],
];
cases.forEach(([definition, value], index) => {
  test(`manually typed DTO agrees with schema ${index}: ${definition}`, () => {
    validateContract(value, "trace", definition);
  });
});
test("manually typed transport agrees with Runner schema", () => {
  validateContract(transport, "runner", "TransportTraceRecord");
});
test("typed Console and Completion literals agree with shared fixtures", () => {
  for (const [name, value] of [
    ["console-stdout", entry],
    ["completion-success", completion],
  ] as const) {
    const fixture = JSON.parse(
      readFileSync(
        new URL(`../../fixtures/valid/${name}.json`, import.meta.url),
        "utf8",
      ),
    );
    assert.deepEqual(value, fixture);
  }
});
// These must remain compile errors. Runtime validation enforces more than the type system.
// @ts-expect-error Python int is a string on the wire.
const invalidInt: Value = { kind: "scalar", type: "int", value: 1 };
// @ts-expect-error Nullable is required, not optional.
const missingNullable: ConsoleEntry = { index: 0, kind: "stdout", text: "x" };
void invalidInt;
void missingNullable;
