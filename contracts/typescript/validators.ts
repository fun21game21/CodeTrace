/** Local schemas only; no transport, execution, or state reconstruction. */
import traceSchema from "../trace-v1.schema.json" with { type: "json" };
import wsSchema from "../ws-v1.schema.json" with { type: "json" };
import runnerSchema from "../runner-v1.schema.json" with { type: "json" };
import { Ajv2020 } from "ajv/dist/2020.js";
import { fullFormats } from "ajv-formats/dist/formats.js";
import type { ValidateFunction } from "ajv";
import type {
  Checkpoint,
  Delta,
  NormalizedState,
  StateDelta,
  TraceDocument,
  WsMessage,
  RunnerMessage,
  Binding,
} from "./types.ts";

export type Contract = "trace" | "ws" | "runner";
export class ContractValidationError extends Error {}

export function validTimestamp(value: string): boolean {
  const match =
    /^([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.[0-9]+)?Z$(?![\s\S])/.exec(
      value,
    );
  if (!match) return false;
  const year = Number(match[1]),
    month = Number(match[2]),
    day = Number(match[3]);
  const hour = Number(match[4]),
    minute = Number(match[5]),
    second = Number(match[6]);
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  return (
    month >= 1 &&
    month <= 12 &&
    day >= 1 &&
    day <= days[month - 1]! &&
    hour <= 23 &&
    minute <= 59 &&
    second <= 59
  );
}

const ajv = new Ajv2020({
  strict: true,
  strictRequired: false,
  strictTypes: false,
  coerceTypes: false,
  useDefaults: false,
  removeAdditional: false,
  allErrors: false,
});
ajv.addFormat("uuid", fullFormats.uuid!);
ajv.addFormat("date-time", { type: "string", validate: validTimestamp });
const schemaIds = new Map<Contract, string>();
const definitions = new Map<Contract, Set<string>>();
const schemas = { trace: traceSchema, ws: wsSchema, runner: runnerSchema };
for (const contract of ["trace", "ws", "runner"] as const) {
  const schema = schemas[contract];
  schemaIds.set(contract, schema.$id);
  definitions.set(contract, new Set(Object.keys(schema.$defs)));
  ajv.addSchema(schema);
}
const validators = new Map<string, ValidateFunction>();
function validator(contract: Contract, definition?: string): ValidateFunction {
  if (definition !== undefined && !definitions.get(contract)?.has(definition)) {
    throw new Error(`Unknown contract definition: ${contract}/${definition}`);
  }
  const key =
    schemaIds.get(contract)! +
    (definition === undefined ? "" : `#/$defs/${definition}`);
  let result = validators.get(key);
  if (!result) {
    result = ajv.getSchema(key) ?? ajv.compile({ $ref: key });
    validators.set(key, result);
  }
  return result;
}
function requireContract(
  condition: boolean,
  message: string,
): asserts condition {
  if (!condition) throw new ContractValidationError(message);
}
function checkJson(value: unknown, ancestors = new Set<object>()): void {
  if (value === null || typeof value === "string" || typeof value === "boolean")
    return;
  if (typeof value === "number") {
    requireContract(Number.isFinite(value), "JSON numbers must be finite");
    return;
  }
  requireContract(
    typeof value === "object" && value !== null,
    "Input must contain only JSON types",
  );
  requireContract(
    Array.isArray(value) ||
      Object.getPrototypeOf(value) === Object.prototype ||
      Object.getPrototypeOf(value) === null,
    "Input must contain only JSON types",
  );
  requireContract(
    !ancestors.has(value),
    "JSON must not contain in-memory cycles; use $ref",
  );
  ancestors.add(value);
  requireContract(
    Reflect.ownKeys(value).every((k) => typeof k === "string"),
    "JSON keys must be strings",
  );
  for (const child of Object.values(value)) checkJson(child, ancestors);
  ancestors.delete(value);
}
function unique(items: string[], label: string): void {
  requireContract(items.length === new Set(items).size, `Duplicate ${label}`);
}
function equal(a: unknown[], b: unknown[]): boolean {
  return a.length === b.length && a.every((item, i) => item === b[i]);
}
function codePointCompare(a: string, b: string): number {
  const left = Array.from(a, (c) => c.codePointAt(0)!),
    right = Array.from(b, (c) => c.codePointAt(0)!);
  for (let i = 0; i < Math.min(left.length, right.length); i++) {
    if (left[i] !== right[i]) return left[i]! - right[i]!;
  }
  return left.length - right.length;
}
function idCompare(a: string, b: string): number {
  return a.length - b.length || codePointCompare(a, b);
}
function bindings(items: Binding[]): void {
  const names = items.map((b) => b.name);
  unique(names, "binding names");
  requireContract(
    equal(names, [...names].sort(codePointCompare)),
    "Bindings must be ordered by Unicode code point",
  );
}
function refs(value: unknown): Set<string> {
  if (typeof value !== "object" || value === null) return new Set();
  if (!Array.isArray(value) && "$ref" in value)
    return new Set([(value as { $ref: string }).$ref]);
  return new Set(Object.values(value).flatMap((v) => [...refs(v)]));
}
function state(value: NormalizedState): void {
  const objects = value.objects.map((o) => o.id),
    frames = value.frames.map((f) => f.id);
  unique(objects, "object IDs");
  unique(frames, "frame IDs");
  unique(value.stack, "stack IDs");
  requireContract(
    equal(objects, [...objects].sort(idCompare)),
    "Object table ID order",
  );
  requireContract(
    equal(frames, [...frames].sort(idCompare)),
    "Frame table ID order",
  );
  requireContract(
    equal([...frames].sort(idCompare), [...value.stack].sort(idCompare)),
    "Active frames must match stack",
  );
  const objectIds = new Set(objects);
  requireContract(
    [...refs(value)].every((r) => objectIds.has(r)),
    "Unresolved object reference",
  );
  const frameById = new Map(value.frames.map((f) => [f.id, f]));
  value.stack.forEach((id, index) => {
    const parent = index === 0 ? null : value.stack[index - 1];
    requireContract(
      frameById.get(id)!.parentId === parent,
      "Invalid active frame parent",
    );
  });
}
function trace(document: TraceDocument): void {
  const { states, events, console } = document,
    interval = document.checkpointInterval;
  requireContract(
    interval === document.limits.checkpoint_interval,
    "Checkpoint interval mismatch",
  );
  states.forEach((record, index) => {
    requireContract(
      record.version === index,
      "State versions must be contiguous from zero",
    );
    requireContract(
      record.kind === (index % interval === 0 ? "checkpoint" : "delta"),
      "Unexpected checkpoint position",
    );
  });
  requireContract(
    events.every((e, i) => e.index === i),
    "Event indices",
  );
  requireContract(
    console.every((e, i) => e.index === i),
    "Console indices",
  );
  requireContract(
    events.filter((e) => e.kind === "RUN_END").length === 1,
    "Exactly one RUN_END required",
  );
  const end = events.at(-1)!;
  requireContract(end.kind === "RUN_END", "RUN_END must be last");
  requireContract(
    end.stateVersion === states.length - 1,
    "RUN_END must use latest state",
  );
  requireContract(
    end.consoleOffset === console.length,
    "RUN_END must include all Console",
  );
  events.forEach((e, i) => {
    requireContract(
      e.stateVersion < states.length &&
        (i === 0 || e.stateVersion >= events[i - 1]!.stateVersion),
      "Event stateVersion",
    );
    requireContract(
      e.consoleOffset <= console.length &&
        (i === 0 || e.consoleOffset >= events[i - 1]!.consoleOffset),
      "Event consoleOffset",
    );
  });
  requireContract(events.length <= document.limits.max_events, "Event limit");
  if (events.length === 1)
    requireContract(
      document.completion.stateAccuracy === "unavailable",
      "No observation",
    );
  if (document.completion.stateAccuracy === "unavailable")
    requireContract(
      events.length === 1,
      "Unavailable requires no accepted observations",
    );
}
function semantic(value: unknown): void {
  if (Array.isArray(value)) {
    value.forEach(semantic);
    return;
  }
  if (typeof value !== "object" || value === null) return;
  const object = value as Record<string, unknown>;
  if ("schemaVersion" in object) trace(value as TraceDocument);
  if (object.kind === "checkpoint") {
    const record = value as Checkpoint;
    requireContract(
      record.version === record.state.version,
      "Checkpoint version mismatch",
    );
  }
  if (object.kind === "delta") {
    const record = value as Delta;
    requireContract(
      record.baseVersion === record.version - 1,
      "Delta baseVersion mismatch",
    );
  }
  if ("framesUpsert" in object) {
    const changes = value as StateDelta;
    for (const prefix of ["frames", "objects"] as const) {
      const upsert = changes[`${prefix}Upsert`].map((r) => r.id),
        remove = changes[`${prefix}Remove`];
      unique(upsert, `${prefix} upsert IDs`);
      unique(remove, `${prefix} remove IDs`);
      const removeIds = new Set(remove);
      requireContract(
        !upsert.some((id) => removeIds.has(id)),
        "ID appears in upsert and remove",
      );
    }
    requireContract(
      changes.globals !== null ||
        changes.stack !== null ||
        changes.eventRoots !== null ||
        changes.framesUpsert.length +
          changes.framesRemove.length +
          changes.objectsUpsert.length +
          changes.objectsRemove.length >
          0,
      "An empty change does not create a Delta",
    );
  }
  if ("objects" in object && "eventRoots" in object && "version" in object)
    state(value as NormalizedState);
  for (const key of ["globals", "locals", "attrs"]) {
    if (Array.isArray(object[key])) bindings(object[key] as Binding[]);
  }
  if (object.type === "float" && object.kind === "scalar") {
    const text = object.value as string;
    requireContract(
      ["nan", "inf", "-inf"].includes(text) || Number.isFinite(Number(text)),
      "Float overflow",
    );
  }
  Object.values(object).forEach(semantic);
}
export function validateContract(
  value: unknown,
  contract: Contract,
  definition?: string,
): void {
  checkJson(value);
  const check = validator(contract, definition);
  if (!check(value))
    throw new ContractValidationError(ajv.errorsText(check.errors));
  semantic(value);
}
export function validateTrace(value: unknown): TraceDocument {
  validateContract(value, "trace");
  return value as TraceDocument;
}
export function validateWs(value: unknown): WsMessage {
  validateContract(value, "ws");
  return value as WsMessage;
}
export function validateRunner(value: unknown): RunnerMessage {
  validateContract(value, "runner");
  return value as RunnerMessage;
}
