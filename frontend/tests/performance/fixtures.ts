/** Deterministic performance data only. Never imported by the production UI. */
import { readFileSync, mkdirSync, writeFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { resolve } from "node:path";
import { tmpdir } from "node:os";
import type {
  TraceDocument,
  NormalizedState,
  Frame,
  ObjectRecord,
  Value,
  StateRecord,
} from "../../../contracts/typescript/index.ts";
import { TraceCore } from "../../src/trace/index.ts";
const base = () =>
  JSON.parse(
    readFileSync(
      new URL(
        "../../../contracts/fixtures/valid/trace-minimal-success.json",
        import.meta.url,
      ),
      "utf8",
    ),
  ) as TraceDocument;
const scalar = (value: string): Value => ({
  kind: "scalar",
  type: "str",
  value,
});
const integer = (n: number): Value => ({
  kind: "scalar",
  type: "int",
  value: String(n),
});
const ref = (n: number): Value => ({ $ref: "o" + n });
export const scenarios = [
  "100k-small",
  "large-list",
  "object-churn",
  "deep-frames",
] as const;
export type Scenario = (typeof scenarios)[number];
export function makeFixture(name: Scenario): {
  trace: TraceDocument;
  maxStateBytes: number;
  maxObjects: number;
  maxFrames: number;
  checkpointSteps: number[];
} {
  const trace = base();
  trace.sourceCode = "# Deterministic PERFORMANCE performance fixture\npass\n";
  const index = scenarios.indexOf(name);
  trace.runId = `8ba106dd-35b6-4945-a5db-e1d3120c743${index}`;
  const module = (trace.states[1] as Extract<StateRecord, { kind: "delta" }>)
    .changes.framesUpsert[0]!;
  const empty = (
    trace.states[0] as Extract<StateRecord, { kind: "checkpoint" }>
  ).state;
  trace.events = [];
  trace.states = [{ kind: "checkpoint", version: 0, state: empty }];
  const versions =
    name === "large-list" ? 40 : name === "100k-small" ? 1024 : 512;
  let before = empty,
    maxStateBytes = 0,
    maxObjects = 0,
    maxFrames = 0;
  const checkpoints: number[] = [];
  for (let v = 1; v <= versions; v++) {
    const frames: Frame[] = [module];
    let objects: ObjectRecord[] = [];
    let globals: NormalizedState["globals"] = [
      { name: "counter", value: integer(v) },
    ];
    if (name === "large-list") {
      objects = [
        {
          id: "o1",
          kind: "list",
          items: Array.from({ length: 1000 }, (_, i) =>
            scalar(
              String(v).padStart(3, "0") +
                ":" +
                String(i).padStart(4, "0") +
                "x".repeat(982),
            ),
          ),
        },
      ];
      globals = [{ name: "data", value: ref(1) }];
    } else if (name === "object-churn") {
      const leafId = 2000 + (v % 256); // Stable registered IDs disappear and later reappear.
      objects = [
        {
          id: "o1",
          kind: "list",
          items: Array.from({ length: 999 }, (_, i) => ref(i + 2)),
        },
      ];
      for (let i = 0; i < 999; i++)
        objects.push({
          id: "o" + (i + 2),
          kind: "list",
          items: [ref(i === 998 ? leafId : i + 1001)],
        });
      for (let i = 0; i < 998; i++)
        objects.push({
          id: "o" + (i + 1001),
          kind: "list",
          items: [scalar("x".repeat(700))],
        });
      objects.push({
        id: "o" + leafId,
        kind: "list",
        items: [scalar("x".repeat(700))],
      });
      objects.sort((a, b) => Number(a.id.slice(1)) - Number(b.id.slice(1)));
      globals = [...globals, { name: "root", value: ref(1) }];
    } else if (name === "deep-frames") {
      for (let i = 2; i <= 128; i++)
        frames.push({
          id: "f" + i,
          parentId: "f" + (i - 1),
          kind: "function",
          name: "recurse",
          qualifiedName: "recurse",
          definitionLine: 2,
          callSite: { fileName: "<codetrace-user>", lineNumber: 2 },
          parameterNames: ["n"],
          localsSource: "own",
          locals: [{ name: "n", value: integer(i === 128 ? v : i) }],
        });
    }
    const state: NormalizedState = {
      version: v,
      globals,
      frames,
      stack: frames.map((f) => f.id),
      objects,
      eventRoots: [],
    };
    const bytes = Buffer.byteLength(JSON.stringify(state));
    maxStateBytes = Math.max(maxStateBytes, bytes);
    maxObjects = Math.max(maxObjects, objects.length);
    maxFrames = Math.max(maxFrames, frames.length);
    if (bytes > trace.limits.state_bytes)
      throw Error(name + " exceeds state_bytes: " + bytes);
    if (v % 128 === 0) {
      trace.states.push({ kind: "checkpoint", version: v, state });
      checkpoints.push(v);
    } else {
      const oldObjects = new Map(before.objects.map((o) => [o.id, o])),
        oldFrames = new Map(before.frames.map((f) => [f.id, f]));
      const ids = new Set(objects.map((o) => o.id)),
        fids = new Set(frames.map((f) => f.id));
      trace.states.push({
        kind: "delta",
        version: v,
        baseVersion: v - 1,
        changes: {
          globals:
            JSON.stringify(globals) === JSON.stringify(before.globals)
              ? null
              : globals,
          stack:
            JSON.stringify(state.stack) === JSON.stringify(before.stack)
              ? null
              : state.stack,
          eventRoots: null,
          framesUpsert: frames.filter(
            (f) => JSON.stringify(f) !== JSON.stringify(oldFrames.get(f.id)),
          ),
          framesRemove: before.frames
            .filter((f) => !fids.has(f.id))
            .map((f) => f.id),
          objectsUpsert: objects.filter(
            (o) => JSON.stringify(o) !== JSON.stringify(oldObjects.get(o.id)),
          ),
          objectsRemove: before.objects
            .filter((o) => !ids.has(o.id))
            .map((o) => o.id),
        },
      });
    }
    before = state;
  }
  const eventCount = name === "100k-small" ? 100000 : 1025;
  trace.console = Array.from({ length: 16 }, (_, index) => ({
    index,
    kind: "stdout" as const,
    text: (
      "line " +
      String(index).padStart(2, "0") +
      " " +
      "c".repeat(118) +
      "\n"
    ).repeat(30),
    requestId: null,
  }));
  for (let i = 0; i < eventCount - 1; i++) {
    const v = 1 + Math.floor((i * versions) / (eventCount - 1));
    trace.events.push({
      index: i,
      kind: "LINE",
      stateVersion: v,
      frameId: name === "deep-frames" ? "f128" : "f1",
      source: { fileName: "<codetrace-user>", lineNumber: 2 },
      consoleOffset: Math.min(16, Math.floor((i * 17) / (eventCount - 1))),
      payload: {},
    });
  }
  const finalVersion = versions + 1;
  trace.states.push({
    kind: "delta",
    version: finalVersion,
    baseVersion: versions,
    changes: {
      globals: null,
      stack: [],
      eventRoots: null,
      framesUpsert: [],
      framesRemove: before.stack,
      objectsUpsert: [],
      objectsRemove: [],
    },
  });
  trace.events.push({
    index: eventCount - 1,
    kind: "RUN_END",
    stateVersion: finalVersion,
    frameId: null,
    source: null,
    consoleOffset: 16,
    payload: {},
  });
  const core = TraceCore.load(trace);
  const checkpointSteps = checkpoints.map(
    (v) => trace.events.findIndex((e) => e.stateVersion === v) + 1,
  );
  for (const step of new Set([
    1,
    eventCount,
    ...checkpointSteps.flatMap((s) => [s - 1, s, s + 1]),
  ]))
    if (step >= 1 && step <= eventCount)
      core.selectStep({ step, requestId: step });
  return { trace, maxStateBytes, maxObjects, maxFrames, checkpointSteps };
}
export function generate(directory: string) {
  mkdirSync(directory, { recursive: true });
  const manifest = scenarios.map((name) => {
    const { trace, ...details } = makeFixture(name),
      raw = JSON.stringify(trace),
      bytes = Buffer.byteLength(raw);
    if (bytes > trace.limits.trace_bytes) throw Error("Trace bytes exceeded");
    writeFileSync(resolve(directory, name + ".json"), raw);
    const item = {
      name,
      ...details,
      runId: trace.runId,
      sourceCode: trace.sourceCode,
      eventCount: trace.events.length,
      stateRecordCount: trace.states.length,
      checkpointInterval: 128,
      bytes,
      sha256: createHash("sha256").update(raw).digest("hex"),
      limits: trace.limits,
      completion: trace.completion,
    };
    console.log(JSON.stringify({ name, bytes, ...details }));
    return item;
  });
  writeFileSync(
    resolve(directory, "manifest.json"),
    JSON.stringify(manifest, null, 2),
  );
}
if (process.argv[1] === new URL(import.meta.url).pathname)
  generate(
    process.argv[2] ?? resolve(tmpdir(), "codetrace-performance-fixtures"),
  );
