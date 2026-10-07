export const SEED = 0x13c0de;
export function percentile(
  values: readonly number[],
  fraction: number,
): number {
  if (
    !values.length ||
    fraction <= 0 ||
    fraction > 1 ||
    values.some((v) => !Number.isFinite(v) || v < 0)
  )
    throw new RangeError("Invalid sample set");
  return [...values].sort((a, b) => a - b)[
    Math.ceil(fraction * values.length) - 1
  ]!;
}
export function stats(values: readonly number[]) {
  return {
    count: values.length,
    p50: percentile(values, 0.5),
    p95: percentile(values, 0.95),
    max: Math.max(...values),
  };
}
export function decision(
  results: readonly { name: string; longTaskMax: number; seekP95: number }[],
) {
  if (
    !results.length ||
    results.some(
      (r) =>
        !Number.isFinite(r.longTaskMax) ||
        !Number.isFinite(r.seekP95) ||
        r.longTaskMax < 0 ||
        r.seekP95 < 0,
    )
  )
    throw Error("Incomplete decision data");
  const reasons = results.flatMap((r) => [
    ...(r.longTaskMax > 50
      ? [`${r.name}: trace main-thread task ${r.longTaskMax} ms > 50`]
      : []),
    ...(r.seekP95 > 150 ? [`${r.name}: Step p95 ${r.seekP95} ms > 150`] : []),
  ]);
  return { workerRequired: reasons.length > 0, reasons };
}
export function sequences(
  total: number,
  checkpointSteps: readonly number[],
  seed = SEED,
) {
  let x = seed >>> 0;
  const next = () => {
    x ^= x << 13;
    x ^= x >>> 17;
    x ^= x << 5;
    return x >>> 0;
  };
  const clamp = (n: number) => Math.max(1, Math.min(total, n));
  const boundaries = [
    1,
    127,
    128,
    129,
    255,
    256,
    Math.floor(total / 2),
    total,
    ...checkpointSteps.flatMap((s) => [s - 1, s, s + 1]),
  ].map(clamp);
  return {
    warmup: Array.from({ length: 20 }, () => 1 + (next() % total)),
    random: Array.from({ length: 200 }, () => 1 + (next() % total)),
    sequential: Array.from({ length: 200 }, (_, i) =>
      clamp(Math.floor(total / 2) + i),
    ),
    reverse: Array.from({ length: 200 }, (_, i) => clamp(total - i)),
    backward: Array.from({ length: 100 }, (_, i) =>
      clamp(total - Math.floor((i * (total - 1)) / 99)),
    ),
    boundary: Array.from(
      { length: 100 },
      (_, i) => boundaries[i % boundaries.length]!,
    ),
  };
}
