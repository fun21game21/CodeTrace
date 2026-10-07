import { validateTrace } from "../../../contracts/typescript/index.ts";
import { TraceIntegrityError, TraceLoadError } from "./errors.ts";
import type { StoredObject, StoredValue, Trace } from "./types.ts";

/** Detach ordinary parsed JSON/DTOs without reading accessors or __proto__ setters.
 * Reflection on Proxy inputs can run traps: callers must supply JSON/ordinary DTOs.
 * Live JS graph cycles are rejected; encoded $ref cycles are ordinary JSON.
 */
function ownFrozenJson(input: unknown): unknown {
  const holder: unknown[] = [];
  const ancestors = new Set<object>();
  type Task =
    | { value: unknown; target: object; key: string }
    | { finish: object; original: object };
  const pending: Task[] = [{ value: input, target: holder, key: "0" }];
  while (pending.length) {
    const task = pending.pop()!;
    if ("finish" in task) {
      Object.freeze(task.finish);
      ancestors.delete(task.original);
      continue;
    }
    const value = task.value;
    let copy: unknown;
    if (
      value === null ||
      typeof value === "string" ||
      typeof value === "boolean" ||
      (typeof value === "number" && Number.isFinite(value))
    )
      copy = value;
    else if (typeof value === "object") {
      if (ancestors.has(value)) throw new TraceLoadError("Live JSON cycle");
      const array = Array.isArray(value);
      const prototype: unknown = Object.getPrototypeOf(value);
      if (
        prototype !== (array ? Array.prototype : Object.prototype) &&
        !(prototype === null && !array)
      )
        throw new TraceLoadError("Non-JSON prototype");
      const descriptors = Object.getOwnPropertyDescriptors(value);
      const keys = Reflect.ownKeys(descriptors);
      const lengthDescriptor = descriptors["length"];
      const length: unknown = lengthDescriptor?.value;
      if (
        array &&
        (!Number.isSafeInteger(length) ||
          typeof length !== "number" ||
          length < 0 ||
          keys.length !== length + 1)
      )
        throw new TraceLoadError("Sparse/extended JSON array");
      for (const key of keys) {
        if (typeof key !== "string")
          throw new TraceLoadError("Symbol JSON key");
        const descriptor = descriptors[key]!;
        if (
          !("value" in descriptor) ||
          ((key !== "length" || !array) && !descriptor.enumerable)
        )
          throw new TraceLoadError(
            "JSON accessors/non-enumerable properties are forbidden",
          );
        if (
          array &&
          key !== "length" &&
          (!/^(0|[1-9][0-9]*)$/.test(key) || Number(key) >= (length as number))
        )
          throw new TraceLoadError("Extended JSON array");
      }
      const container: object = array ? [] : {};
      copy = container;
      ancestors.add(value);
      pending.push({ finish: container, original: value });
      for (let i = keys.length - 1; i >= 0; i--) {
        const key = keys[i] as string;
        if (array && key === "length") continue;
        pending.push({
          value: descriptors[key]!.value as unknown,
          target: container,
          key,
        });
      }
    } else throw new TraceLoadError("Non-JSON value");
    Object.defineProperty(task.target, task.key, {
      value: copy,
      enumerable: true,
      writable: true,
      configurable: true,
    });
  }
  return holder[0];
}

export function loadTrace(input: unknown): Trace {
  try {
    // CONTRACTS owns all schema, metadata, index, checkpoint, offset and ID checks.
    const trace: Trace = validateTrace(ownFrozenJson(input));
    const initial = trace.states[0];
    if (
      !initial ||
      initial.kind !== "checkpoint" ||
      initial.version !== 0 ||
      initial.state.version !== 0 ||
      [
        initial.state.globals,
        initial.state.frames,
        initial.state.stack,
        initial.state.objects,
        initial.state.eventRoots,
      ].some((a) => a.length !== 0)
    )
      throw new TraceLoadError("Checkpoint 0 must contain the empty state");
    return trace;
  } catch (error) {
    if (error instanceof TraceLoadError) throw error;
    throw new TraceLoadError("Invalid TraceDocument: " + String(error), {
      cause: error,
    });
  }
}

export function objectValues(record: StoredObject): readonly StoredValue[] {
  switch (record.kind) {
    case "dict":
      return record.entries.flatMap((entry) => [entry.key, entry.value]);
    case "instance":
      return record.attrs.map((binding) => binding.value);
    case "opaque":
      return [];
    default:
      return record.items;
  }
}

export function identityOrder(a: string, b: string): number {
  return a.length - b.length || (a < b ? -1 : a > b ? 1 : 0);
}
export function nameOrder(a: string, b: string): number {
  const aa = Array.from(a, (c) => c.codePointAt(0)!);
  const bb = Array.from(b, (c) => c.codePointAt(0)!);
  for (let i = 0; i < Math.min(aa.length, bb.length); i++) {
    if (aa[i] !== bb[i]) return aa[i]! - bb[i]!;
  }
  return aa.length - bb.length;
}
export function integrity(
  condition: boolean,
  message: string,
): asserts condition {
  if (!condition) throw new TraceIntegrityError(message);
}
