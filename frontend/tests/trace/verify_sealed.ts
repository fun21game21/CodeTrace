/** Test process only. Production Trace Core has no file/process/backend dependencies. */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { TraceCore } from "../../src/trace/index.ts";
import type { Fixture } from "./helpers.ts";
import { shuffled } from "./helpers.ts";

const tracePath = process.argv[2];
const expectedPath = process.argv[3];
assert.ok(
  tracePath && expectedPath,
  "Expected exact sealed JSON path and Python oracle path",
);
const input: unknown = JSON.parse(readFileSync(tracePath, "utf8"));
const expected = JSON.parse(
  readFileSync(expectedPath, "utf8"),
) as Fixture["expected"];
const core = TraceCore.load(input);
assert.equal(core.totalSteps, expected.length);
let comparisons = 0;
const forward = Array.from({ length: expected.length }, (_, i) => i + 1);
for (const step of [
  ...forward,
  ...forward.toReversed(),
  ...shuffled(expected.length),
]) {
  const view = core.selectStep({ step, requestId: step });
  const oracle = expected[step - 1]!;
  for (const key of Object.keys(oracle) as (keyof typeof oracle)[])
    assert.deepEqual(view[key], oracle[key], `sealed Step ${step}: ${key}`);
  comparisons++;
}
const end = core.selectStep({ step: core.totalSteps, requestId: 0 });
assert.equal(end.event.kind, "RUN_END");
assert.ok(end.completion);
console.log(
  JSON.stringify({
    steps: core.totalSteps,
    comparisons,
    outcome: end.completion.outcome,
    reason: end.completion.reason,
  }),
);
