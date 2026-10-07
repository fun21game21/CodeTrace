import assert from "node:assert/strict";
import test from "node:test";
import { percentile, stats, decision, sequences, SEED } from "./metrics.ts";
import { TraceCore } from "../../src/trace/index.ts";
import { DirectTraceSelectionExecutor } from "../../src/viewer/executor.ts";
import { fixture } from "../trace/helpers.ts";

test("nearest-rank p95 on exact small samples", () =>
  assert.equal(
    percentile(
      Array.from({ length: 20 }, (_, i) => i + 1),
      0.95,
    ),
    19,
  ));
test("percentile sorts without mutating raw order", () => {
  const raw = [100, 2, 1, 3];
  assert.equal(percentile(raw, 0.5), 2);
  assert.deepEqual(raw, [100, 2, 1, 3]);
});
test("p95 is not the arithmetic average", () =>
  assert.equal(stats([...Array<number>(19).fill(1), 100]).p95, 1));
test("summary preserves count, p50, p95 and max", () =>
  assert.deepEqual(stats([1, 2, 3, 4]), { count: 4, p50: 2, p95: 4, max: 4 }));
for (const raw of [[], [-1], [NaN], [Infinity]])
  test("reject invalid samples " + String(raw), () =>
    assert.throws(() => stats(raw)),
  );
for (const [task, p95, required] of [
  [50, 150, false],
  [50.001, 100, true],
  [1, 150.001, true],
  [49, 149, false],
] as const)
  test(`threshold ${task}/${p95}`, () =>
    assert.equal(
      decision([{ name: "fixture", longTaskMax: task, seekP95: p95 }])
        .workerRequired,
      required,
    ));
test("scenario aggregation uses any threshold breach", () =>
  assert.equal(
    decision([
      { name: "a", longTaskMax: 0, seekP95: 10 },
      { name: "b", longTaskMax: 51, seekP95: 10 },
    ]).workerRequired,
    true,
  ));
test("decision retains scenario and concrete reason", () =>
  assert.match(
    decision([
      { name: "large-list", longTaskMax: 80, seekP95: 200 },
    ]).reasons.join("\n"),
    /large-list.*80 ms > 50/,
  ));
test("missing or nonfinite decision data cannot pass", () => {
  assert.throws(() => decision([]));
  assert.throws(() =>
    decision([{ name: "bad", longTaskMax: NaN, seekP95: 1 }]),
  );
});
test("seeded sequences are reproducible", () =>
  assert.deepEqual(
    sequences(100000, [12404]),
    sequences(100000, [12404], SEED),
  ));
test("different seeds preserve categories but change random targets", () =>
  assert.notDeepEqual(
    sequences(100000, [], 1).random,
    sequences(100000, [], 2).random,
  ));
test("20 warmup and 200 measured random targets remain separate", () => {
  const s = sequences(100000, []);
  assert.equal(s.warmup.length, 20);
  assert.equal(s.random.length, 200);
  assert.notDeepEqual(s.random.slice(0, 20), s.warmup);
});
test("all generated targets within trace", () => {
  for (const values of Object.values(sequences(1025, [255, 511, 1023])))
    assert(values.every((s) => s >= 1 && s <= 1025 && Number.isInteger(s)));
});
test("boundary series covers ends, event and distant state boundaries", () => {
  const s = sequences(100000, [12404, 49903, 99903]).boundary;
  for (const target of [
    1, 127, 128, 129, 255, 256, 50000, 100000, 12403, 12404, 12405, 99902,
    99903, 99904,
  ])
    assert(s.includes(target));
});
test("sequential series means Next, reverse series means Previous", () => {
  const s = sequences(100000, []);
  for (let i = 1; i < 200; i++) {
    assert.equal(s.sequential[i], s.sequential[i - 1]! + 1);
    assert.equal(s.reverse[i], s.reverse[i - 1]! - 1);
  }
});
test("large backward jumps remain distinct from reverse-adjacent", () => {
  const s = sequences(100000, []);
  assert.equal(s.backward.length, 100);
  assert.equal(s.backward.at(-1), 1);
  assert(s.backward[0]! - s.backward[1]! > 100);
});
test("JSON report retains environment, raw samples and separate load/seek stats", () => {
  const report = {
    environment: {
      os: "test",
      architecture: "arm64",
      browser: "Chromium",
      node: "24",
      production: true,
    },
    load: { parseMs: 60 },
    seek: stats([1, 2, 3]),
    samples: [1, 2, 3],
  };
  assert.deepEqual(JSON.parse(JSON.stringify(report)), report);
  assert.equal(report.seek.max, 3);
  assert.equal(
    decision([
      {
        name: "load",
        longTaskMax: report.load.parseMs,
        seekP95: report.seek.p95,
      },
    ]).workerRequired,
    true,
  );
});
test("direct adapter has unchanged TRACE semantics and request identity", async () => {
  const core = TraceCore.load(fixture("alias-128").trace),
    adapter = new DirectTraceSelectionExecutor(core);
  for (const step of [1, core.totalSteps, 2])
    for (const requestedFrameId of [undefined, null, "f1"]) {
      const r = {
        step,
        requestId: 37,
        ...(requestedFrameId === undefined ? {} : { requestedFrameId }),
      };
      assert.deepEqual(await adapter.select(r), core.selectStep(r));
    }
});
