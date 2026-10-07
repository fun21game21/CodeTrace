import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { TraceCore } from "../../src/trace/index.ts";
import type { SelectedStepView } from "../../src/trace/index.ts";
import type { TraceDocument } from "../../../contracts/typescript/index.ts";

export interface Fixture {
  name: string;
  oracle: string;
  trace: TraceDocument;
  expected: Omit<
    SelectedStepView,
    | "runId"
    | "requestId"
    | "step"
    | "totalSteps"
    | "changedObjectIds"
    | "changedBindingNames"
    | "arrayPositionChanges"
  >[];
}
export interface ManifestEntry {
  file: string;
  scenario: string;
  interval: number;
  steps: number;
  oracle: string;
}
export const manifest = JSON.parse(
  readFileSync(new URL("fixtures/manifest.json", import.meta.url), "utf8"),
) as ManifestEntry[];
export function fixture(name: string): Fixture {
  return JSON.parse(
    readFileSync(new URL(`fixtures/${name}.json`, import.meta.url), "utf8"),
  ) as Fixture;
}
export function minimal(): TraceDocument {
  return JSON.parse(
    readFileSync(
      new URL(
        "../../../contracts/fixtures/valid/trace-minimal-success.json",
        import.meta.url,
      ),
      "utf8",
    ),
  ) as TraceDocument;
}
export function shuffled(length: number): number[] {
  const order = Array.from({ length }, (_, i) => i + 1);
  let seed = 104729;
  for (let i = length - 1; i > 0; i--) {
    seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
    const j = seed % (i + 1);
    [order[i], order[j]] = [order[j]!, order[i]!];
  }
  return order;
}
export function verify(input: Fixture, order: readonly number[]): number {
  const core = TraceCore.load(input.trace);
  assert.equal(core.sourceCode, input.trace.sourceCode);
  assert.equal(core.inspect().materializedStateCount, 0);
  const changes = new Map<
    number,
    readonly [readonly string[], readonly string[]]
  >();
  for (const step of order) {
    const view = core.selectStep({ step, requestId: step + 1000 });
    const expected = input.expected[step - 1]!;
    for (const key of Object.keys(expected) as (keyof typeof expected)[])
      assert.deepEqual(
        view[key],
        expected[key],
        `${input.name}: step ${step}, ${key}`,
      );
    assert.equal(view.step, step);
    assert.equal(view.totalSteps, input.trace.events.length);
    assert.equal(view.requestId, step + 1000);
    assert.equal(view.runId, input.trace.runId);
    const pair = [view.changedBindingNames, view.changedObjectIds] as const;
    if (changes.has(step)) assert.deepEqual(pair, changes.get(step));
    changes.set(step, pair);
    assert.ok(core.inspect().materializedStateCount <= 2);
  }
  return order.length;
}
export function frozen(value: unknown): void {
  if (value === null || typeof value !== "object") return;
  assert.ok(Object.isFrozen(value));
  for (const child of Object.values(value)) frozen(child);
}
