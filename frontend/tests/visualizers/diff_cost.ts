import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { TraceCore } from "../../src/trace/index.ts";
const results = [];
for (const name of [
  "100k-small",
  "large-list",
  "object-churn",
  "deep-frames",
]) {
  const trace = JSON.parse(
    readFileSync(
      join(tmpdir(), "codetrace-performance-fixtures", name + ".json"),
      "utf8",
    ),
  );
  const core = TraceCore.load(trace),
    samples = [];
  const targets = [
    1,
    2,
    3,
    Math.floor(core.totalSteps / 2),
    Math.floor(core.totalSteps / 2) + 1,
    core.totalSteps - 1,
    core.totalSteps,
  ];
  for (const step of targets) {
    const start = performance.now();
    const v = core.selectStep({ step, requestId: 1 });
    const selectionMs = performance.now() - start;
    samples.push({
      step,
      selectionMs,
      entries: v.arrayPositionChanges.length,
      positions: v.arrayPositionChanges.reduce(
        (n, e) => n + e.indices.length,
        0,
      ),
      diffJsonBytes: Buffer.byteLength(JSON.stringify(v.arrayPositionChanges)),
    });
  }
  results.push({ name, samples });
}
mkdirSync(new URL("../../benchmarks/results", import.meta.url), {
  recursive: true,
});
writeFileSync(
  new URL(
    "../../benchmarks/results/visualizers-diff-cost.json",
    import.meta.url,
  ),
  JSON.stringify(
    {
      scope:
        "Seven representative Steps per fixture, not an exhaustive maximum; Node selectStep time includes reconstruction and existing diff, not isolated new-field CPU cost.",
      results,
    },
    null,
    2,
  ) + "\n",
);
console.log(
  JSON.stringify(
    results.map((r) => ({
      name: r.name,
      maxSampleDiffBytes: Math.max(...r.samples.map((s) => s.diffJsonBytes)),
      maxSampleSelectMs: Math.max(...r.samples.map((s) => s.selectionMs)),
    })),
  ),
);
