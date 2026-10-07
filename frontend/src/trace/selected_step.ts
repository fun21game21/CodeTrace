import type {
  Immutable,
  Bindings,
  Highlight,
  State,
  StoredFrame,
  StoredObject,
  StoredValue,
  SelectedStepView,
  StepRequest,
} from "./types.ts";
import type { TraceEvent } from "../../../contracts/typescript/index.ts";
import { StateReconstructor } from "./state_reconstructor.ts";
import { TraceStore } from "./trace_store.ts";
import {
  integrity,
  identityOrder,
  loadTrace,
  nameOrder,
  objectValues,
} from "./validation.ts";
import { StepOutOfRangeError } from "./errors.ts";

/** Fixed-depth encoded DTO comparison: keys canonicalized, array order preserved. */
function canonical(value: unknown): string {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return "[" + value.map(canonical).join(",") + "]";
  const data = value as Readonly<Record<string, unknown>>;
  return (
    "{" +
    Object.keys(data)
      .sort()
      .map((key) => JSON.stringify(key) + ":" + canonical(data[key]))
      .join(",") +
    "}"
  );
}
function changedObjects(
  current: State,
  previous: State | null,
): readonly string[] {
  if (previous?.version === current.version) return Object.freeze([]);
  const old = new Map(previous?.objects.map((o) => [o.id, o]));
  const changed = new Set<string>();
  for (const object of current.objects) {
    if (
      !old.has(object.id) ||
      canonical(object) !== canonical(old.get(object.id))
    )
      changed.add(object.id);
    old.delete(object.id);
  }
  for (const id of old.keys()) changed.add(id);
  return Object.freeze([...changed].sort(identityOrder));
}
function affectedObjects(
  current: State,
  previous: State | null,
  changed: readonly string[],
): Set<string> {
  const parents = new Map<string, Set<string>>();
  const add = (objects: readonly StoredObject[]) => {
    for (const object of objects)
      for (const value of objectValues(object))
        if ("$ref" in value) {
          let ids = parents.get(value.$ref);
          if (!ids) {
            ids = new Set();
            parents.set(value.$ref, ids);
          }
          ids.add(object.id);
        }
  };
  add(current.objects);
  if (previous) add(previous.objects);
  const affected = new Set(changed);
  const pending = [...changed];
  while (pending.length)
    for (const parent of parents.get(pending.pop()!) ?? []) {
      if (!affected.has(parent)) {
        affected.add(parent);
        pending.push(parent);
      }
    }
  return affected;
}
function changedNames(
  current: Bindings,
  previous: Bindings,
  affected: ReadonlySet<string>,
): string[] {
  const old = new Map(previous.map((b) => [b.name, b.value]));
  return current
    .filter(
      (b) =>
        !old.has(b.name) ||
        canonical(b.value) !== canonical(old.get(b.name)) ||
        ("$ref" in b.value && affected.has(b.value.$ref)),
    )
    .map((b) => b.name);
}
function equalValue(a: StoredValue, b: StoredValue): boolean {
  return "$ref" in a
    ? "$ref" in b && a.$ref === b.$ref
    : !("$ref" in b) && a.type === b.type && a.value === b.value;
}
function arrayChanges(
  current: State,
  previous: State | null,
  affected: ReadonlySet<string>,
): SelectedStepView["arrayPositionChanges"] {
  if (previous?.version === current.version) return Object.freeze([]);
  const old = new Map(previous?.objects.map((o) => [o.id, o]));
  const entries: { objectId: string; indices: readonly number[] }[] = [];
  for (const object of current.objects) {
    if (object.kind !== "list") continue;
    const before = old.get(object.id);
    const indices: number[] = [];
    for (let i = 0; i < Math.min(200, object.items.length); i++) {
      const value = object.items[i]!;
      const prior = before?.kind === "list" ? before.items[i] : undefined;
      if (
        !prior ||
        !equalValue(value, prior) ||
        ("$ref" in value && affected.has(value.$ref))
      )
        indices.push(i);
    }
    if (indices.length)
      entries.push(
        Object.freeze({ objectId: object.id, indices: Object.freeze(indices) }),
      );
  }
  return Object.freeze(
    entries.sort((a, b) => identityOrder(a.objectId, b.objectId)),
  );
}
function scope(
  state: State,
  requested: string | null | undefined,
): StoredFrame | null {
  if (requested === null) return null;
  const id = requested ?? state.stack.at(-1);
  return (
    state.frames.find((f) => f.id === id) ??
    state.frames.find((f) => f.id === state.stack.at(-1)) ??
    null
  );
}
function locals(frame: StoredFrame | null, state: State): Bindings {
  return frame === null
    ? Object.freeze([])
    : frame.localsSource === "globals"
      ? state.globals
      : frame.locals;
}
function highlight(event: Immutable<TraceEvent>, store: TraceStore): Highlight {
  const source =
    event.kind === "RUN_END"
      ? store.trace.completion.primaryDiagnostic?.source
      : event.source;
  const lineNumber = source?.lineNumber ?? null;
  return Object.freeze({
    lineNumber,
    mode:
      lineNumber === null
        ? "none"
        : event.kind === "RUN_END"
          ? "error"
          : event.kind === "LINE"
            ? "before_line"
            : "event",
  });
}
function checkEvent(event: Immutable<TraceEvent>, state: State): void {
  if (event.kind === "RUN_END") return;
  integrity(
    state.stack.at(-1) === event.frameId,
    `Event ${event.index}: frame is not the active top frame`,
  );
  if (event.kind === "RETURN" && event.payload.returnValueKnown)
    integrity(
      state.eventRoots.some(
        (value) => canonical(value) === canonical(event.payload.returnValue),
      ),
      `Event ${event.index}: known return value missing from eventRoots`,
    );
}

export class TraceCore {
  #store: TraceStore;
  #reconstructor: StateReconstructor;
  private constructor(store: TraceStore) {
    this.#store = store;
    this.#reconstructor = new StateReconstructor(store);
  }
  static load(input: unknown): TraceCore {
    return new TraceCore(new TraceStore(loadTrace(input)));
  }
  get sourceCode(): string {
    return this.#store.trace.sourceCode;
  }
  get runId(): string {
    return this.#store.trace.runId;
  }
  get totalSteps(): number {
    return this.#store.trace.events.length;
  }
  /** Counts only, so callers cannot mutate internal maps/indexes. Not a performance benchmark. */
  inspect(): Readonly<{
    eventCount: number;
    stateRecordCount: number;
    checkpointCount: number;
    materializedStateCount: number;
  }> {
    return Object.freeze({
      eventCount: this.totalSteps,
      stateRecordCount: this.#store.trace.states.length,
      checkpointCount: this.#store.checkpointVersions.length,
      materializedStateCount: this.#reconstructor.cachedStateCount,
    });
  }
  selectStep(request: StepRequest): SelectedStepView {
    if (
      !Number.isSafeInteger(request.step) ||
      request.step < 1 ||
      request.step > this.totalSteps
    )
      throw new StepOutOfRangeError(request.step, this.totalSteps);
    if (!Number.isSafeInteger(request.requestId) || request.requestId < 0)
      throw new RangeError("requestId must be a nonnegative safe integer");
    const event = this.#store.event(request.step - 1);
    const predecessor =
      request.step > 1 ? this.#store.event(request.step - 2) : null;
    const [state, previous] = this.#reconstructor.statesFor(
      event.stateVersion,
      predecessor?.stateVersion ?? null,
    );
    checkEvent(event, state);
    if (predecessor && previous) checkEvent(predecessor, previous);
    const selected = scope(state, request.requestedFrameId);
    const selectedLocals = locals(selected, state);
    const previousFrame =
      previous?.frames.find((f) => f.id === selected?.id) ?? null;
    const changedObjectIds = changedObjects(state, previous);
    const affected = affectedObjects(state, previous, changedObjectIds);
    const changedBindingNames = Object.freeze(
      [
        ...new Set([
          ...changedNames(state.globals, previous?.globals ?? [], affected),
          ...changedNames(
            selectedLocals,
            previous ? locals(previousFrame, previous) : [],
            affected,
          ),
        ]),
      ].sort(nameOrder),
    );
    const frames = new Map(state.frames.map((f) => [f.id, f]));
    return Object.freeze({
      runId: this.runId,
      requestId: request.requestId,
      step: request.step,
      totalSteps: this.totalSteps,
      event,
      state,
      selectedFrameId: selected?.id ?? null,
      globals: state.globals,
      selectedLocals,
      callStack: Object.freeze(state.stack.map((id) => frames.get(id)!)),
      consoleEntries: Object.freeze(
        this.#store.trace.console.slice(0, event.consoleOffset),
      ),
      changedObjectIds,
      arrayPositionChanges: arrayChanges(state, previous, affected),
      changedBindingNames,
      highlight: highlight(event, this.#store),
      eventDiagnostic:
        event.kind === "EXCEPTION" ? event.payload.diagnostic : null,
      completion:
        event.kind === "RUN_END" ? this.#store.trace.completion : null,
    });
  }
}
