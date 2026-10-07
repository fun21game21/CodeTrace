import { decision, sequences, stats, SEED } from "./metrics.ts";
export interface Evidence {
  version: number;
  mode: "direct" | "worker";
  date: string;
  seed: number;
  environment: {
    production: boolean;
    os: string;
    architecture: string;
    userAgent: string;
    node: string;
    longTasksApi: boolean;
    [key: string]: unknown;
  };
  results: {
    name: string;
    bytes: number;
    sha256: string;
    eventCount: number;
    stateRecordCount: number;
    checkpointInterval: number;
    maxStateBytes: number;
    maxObjects: number;
    maxFrames: number;
    checkpointSteps: number[];
    warmupCount: number;
    longTaskMax: number;
    syncFallbackMax: number | null;
    seekP95: number;
    load: {
      fetchMs: number;
      jsonParseMs: number;
      traceCoreLoadMs: number;
      traceReadyMs: number;
      longTasks: { duration: number }[];
      [key: string]: unknown;
    };
    categories: Record<
      string,
      {
        summary: ReturnType<typeof stats>;
        samples: {
          step: number;
          frame?: string | null;
          endToEndMs: number;
          selectionMs: number;
          commitMs?: number;
          firstRenderMs?: number;
          workerSelectionMs?: number;
          freezeMs?: number;
        }[];
        longTasks: { duration: number }[];
      }
    >;
    serverNetwork?: { gets: number; releases: number };
    mainTraceHandlePresent?: boolean;
    workerSessionPresent?: boolean;
    [key: string]: unknown;
  }[];
}
function check(condition: boolean, message: string): asserts condition {
  if (!condition) throw Error(message);
}
export function validateEvidence(report: Evidence) {
  check(
    report.version === 1 &&
      Number.isFinite(Date.parse(report.date)) &&
      report.seed === SEED,
    "Report version/date/seed",
  );
  const env = report.environment;
  check(
    env.production &&
      ["os", "architecture", "userAgent", "node"].every(
        (k) => typeof env[k] === "string" && String(env[k]).length > 0,
      ),
    "Environment metadata",
  );
  check(
    report.results.length === 4 &&
      new Set(report.results.map((r) => r.name)).size === 4,
    "Four unique scenarios required",
  );
  for (const r of report.results) {
    check(
      r.bytes > 0 &&
        r.bytes <= 52428800 &&
        r.checkpointInterval === 128 &&
        r.stateRecordCount >= 1 &&
        /^[0-9a-f]{64}$/.test(r.sha256),
      "Trace metadata",
    );
    check(r.warmupCount >= 20, "Warmup count");
    check(
      [
        r.load.fetchMs,
        r.load.jsonParseMs,
        r.load.traceCoreLoadMs,
        r.load.traceReadyMs,
      ].every((v) => Number.isFinite(v) && v >= 0),
      "Separate load stages",
    );
    const targets = sequences(r.eventCount, r.checkpointSteps, report.seed);
    for (const [name, steps] of Object.entries(targets)) {
      if (name === "warmup") continue;
      const category = r.categories[name];
      check(!!category, "Missing category " + name);
      check(
        category.samples.length === steps.length &&
          category.samples.every((s, i) => s.step === steps[i]),
        "Seeded targets mismatch",
      );
      check(
        JSON.stringify(category.summary) ===
          JSON.stringify(stats(category.samples.map((s) => s.endToEndMs))),
        "Summary not computed from raw samples",
      );
    }
    check(
      r.seekP95 ===
        Math.max(...Object.values(r.categories).map((c) => c.summary.p95)),
      "Seek p95 aggregation",
    );
    if (r.name === "100k-small")
      check(r.eventCount === 100000, "100k events must be actual");
    if (r.name === "large-list")
      check(r.maxStateBytes >= 1000000, "Near-limit list required");
    if (r.name === "object-churn")
      check(r.maxObjects >= 1900, "Object churn load required");
    if (r.name === "deep-frames")
      check(
        r.maxFrames === 128 && r.categories.frame?.samples.length === 100,
        "Deep frames/reselection",
      );
  }
  return decision(
    report.results.map((r) => ({
      name: r.name,
      longTaskMax: r.syncFallbackMax ?? r.longTaskMax,
      seekP95: r.seekP95,
    })),
  );
}
export function compareEvidence(direct: Evidence, worker?: Evidence) {
  const result = validateEvidence(direct);
  check(direct.mode === "direct", "Baseline must be direct");
  if (!result.workerRequired) {
    check(worker === undefined, "Worker forbidden after passing baseline");
    return result;
  }
  check(worker !== undefined, "Worker evidence required");
  validateEvidence(worker);
  check(worker.mode === "worker", "Post run must use Worker");
  for (const d of direct.results) {
    const w = worker.results.find((r) => r.name === d.name);
    check(!!w, "Missing Worker scenario");
    check(
      d.sha256 === w.sha256 &&
        d.bytes === w.bytes &&
        d.eventCount === w.eventCount &&
        d.stateRecordCount === w.stateRecordCount,
      "Fixture changed after baseline",
    );
    check(w.seekP95 <= 150, "Worker p95 exceeds target");
    check(w.longTaskMax <= 50, "Worker main-thread tasks still exceed target");
    check(
      w.mainTraceHandlePresent === false && w.workerSessionPresent === true,
      "One trace owner violated",
    );
    check(
      w.serverNetwork?.gets === 1 && w.serverNetwork.releases === 1,
      "Expected single GET and release",
    );
  }
  return {
    ...result,
    executionAdapter: "single-worker" as const,
    postWorkerPass: true,
  };
}
