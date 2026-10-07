import assert from "node:assert/strict";
import test from "node:test";
import { TraceCore } from "../../src/trace/index.ts";
import { fixture, frozen, manifest, shuffled, verify } from "./helpers.ts";

for (const entry of manifest) {
  for (const mode of ["forward", "reverse", "random"] as const) {
    test(`Python oracle ${entry.file}: ${mode}, every Step`, () => {
      const input = fixture(entry.file.replace(/\.json$/, ""));
      const forward = Array.from({ length: entry.steps }, (_, i) => i + 1);
      const order =
        mode === "forward"
          ? forward
          : mode === "reverse"
            ? forward.reverse()
            : shuffled(entry.steps);
      verify(input, [...order, 1, entry.steps, 1]);
    });
  }
}
test("fixture manifest covers all independent scenarios and intervals", () => {
  assert.equal(manifest.length, 75);
  assert.ok(
    manifest.every((m) => m.oracle.includes("ENCODER independent decoder")),
  );
  assert.equal(new Set(manifest.map((m) => m.file)).size, manifest.length);
  assert.ok(manifest.every((m) => [1, 2, 128].includes(m.interval)));
  assert.equal(new Set(manifest.map((m) => m.scenario)).size, 25);
});
for (const interval of [1, 2, 128]) {
  test(`boundary/direct seeks 127/128/129/255/256, interval ${interval}`, () => {
    const input = fixture(`boundaries-${interval}`);
    verify(input, [127, 129, 128, 256, 1, 255, 257, 100, 200, 100]);
    const core = TraceCore.load(input.trace);
    const first = core.selectStep({ step: 100, requestId: 1 });
    const original = JSON.stringify(first);
    core.selectStep({ step: 200, requestId: 2 });
    assert.equal(JSON.stringify(first), original);
    assert.deepEqual(core.selectStep({ step: 100, requestId: 1 }), first);
    frozen(first);
  });
}
