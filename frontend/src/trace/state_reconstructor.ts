import type {
  Immutable,
  Bindings,
  State,
  StoredFrame,
  StoredObject,
  StoredValue,
} from "./types.ts";
import type { StateRecord } from "../../../contracts/typescript/index.ts";
import { identityOrder, integrity, objectValues } from "./validation.ts";
import type { TraceStore } from "./trace_store.ts";

interface WorkingState {
  version: number;
  globals: Bindings;
  frames: Map<string, StoredFrame>;
  stack: readonly string[];
  objects: Map<string, StoredObject>;
  eventRoots: readonly StoredValue[];
}
function working(state: State): WorkingState {
  return {
    ...state,
    globals: Object.freeze([...state.globals]),
    stack: Object.freeze([...state.stack]),
    eventRoots: Object.freeze([...state.eventRoots]),
    frames: new Map(state.frames.map((f) => [f.id, f])),
    objects: new Map(state.objects.map((o) => [o.id, o])),
  };
}
function validate(state: WorkingState): void {
  const prefix = `State ${state.version}: `;
  integrity(
    new Set(state.stack).size === state.stack.length,
    prefix + "duplicate stack frame",
  );
  integrity(
    state.frames.size === state.stack.length,
    prefix + "frames differ from active stack",
  );
  state.stack.forEach((id, index) => {
    const frame = state.frames.get(id);
    integrity(frame !== undefined, prefix + `missing frame ${id}`);
    integrity(
      frame.parentId === (state.stack[index - 1] ?? null),
      prefix + `invalid parent chain ${id}`,
    );
    integrity(
      frame.kind !== "module" ||
        (frame.localsSource === "globals" && frame.locals.length === 0),
      prefix + "module locals must use globals",
    );
  });
  const check = (values: readonly StoredValue[]) => {
    for (const value of values)
      if ("$ref" in value)
        integrity(
          state.objects.has(value.$ref),
          prefix + `unresolved object ${value.$ref}`,
        );
  };
  check(state.globals.map((b) => b.value));
  check(state.eventRoots);
  for (const frame of state.frames.values())
    check(frame.locals.map((b) => b.value));
  // Every encoded edge is checked once. No traversal of live objects or recursive refs.
  for (const object of state.objects.values()) check(objectValues(object));
}
function apply(
  before: WorkingState,
  record: Immutable<StateRecord>,
): WorkingState {
  integrity(
    record.kind === "delta",
    `Expected delta at version ${record.version}`,
  );
  integrity(
    record.baseVersion === before.version &&
      record.version === before.version + 1,
    `Delta ${record.version}: wrong baseVersion`,
  );
  const changes = record.changes;
  const next: WorkingState = {
    version: record.version,
    globals:
      changes.globals === null
        ? before.globals
        : Object.freeze([...changes.globals]),
    stack:
      changes.stack === null ? before.stack : Object.freeze([...changes.stack]),
    eventRoots:
      changes.eventRoots === null
        ? before.eventRoots
        : Object.freeze([...changes.eventRoots]),
    frames: new Map(before.frames),
    objects: new Map(before.objects),
  };
  for (const id of changes.framesRemove)
    integrity(
      next.frames.delete(id),
      `Delta ${record.version}: remove missing frame ${id}`,
    );
  for (const id of changes.objectsRemove)
    integrity(
      next.objects.delete(id),
      `Delta ${record.version}: remove missing object ${id}`,
    );
  for (const frame of changes.framesUpsert) next.frames.set(frame.id, frame);
  for (const object of changes.objectsUpsert)
    next.objects.set(object.id, object);
  validate(next); // All replacements/removes/upserts form one transaction.
  return next;
}
function publish(state: WorkingState): State {
  return Object.freeze({
    ...state,
    frames: Object.freeze(
      [...state.frames.values()].sort((a, b) => identityOrder(a.id, b.id)),
    ),
    objects: Object.freeze(
      [...state.objects.values()].sort((a, b) => identityOrder(a.id, b.id)),
    ),
  });
}
export class StateReconstructor {
  #store: TraceStore;
  #cache: readonly State[] = [];
  constructor(store: TraceStore) {
    this.#store = store;
  }
  get cachedStateCount(): number {
    return this.#cache.length;
  }
  #reconstruct(version: number): State {
    const checkpoint = this.#store.record(this.#store.checkpoint(version));
    integrity(
      checkpoint.kind === "checkpoint",
      "Checkpoint index points to delta",
    );
    let state = working(checkpoint.state);
    validate(state);
    for (let v = checkpoint.version + 1; v <= version; v++)
      state = apply(state, this.#store.record(v));
    return publish(state);
  }
  statesFor(
    version: number,
    predecessorVersion: number | null,
  ): readonly [State, State | null] {
    // Discard old selections before allocating. No cache proportional to trace size.
    this.#cache = [];
    const current = this.#reconstruct(version);
    const previous =
      predecessorVersion === null
        ? null
        : predecessorVersion === version
          ? current
          : this.#reconstruct(predecessorVersion);
    this.#cache = Object.freeze(
      previous && previous !== current ? [current, previous] : [current],
    );
    return [current, previous];
  }
}
