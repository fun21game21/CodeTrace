import type { Immutable, Trace } from "./types.ts";
import type {
  StateRecord,
  TraceEvent,
} from "../../../contracts/typescript/index.ts";
import { integrity } from "./validation.ts";

/** Arrays already form dense version/index lookups; only checkpoint IDs are indexed separately. */
export class TraceStore {
  readonly trace: Trace;
  readonly checkpointVersions: readonly number[];
  constructor(trace: Trace) {
    this.trace = trace;
    this.checkpointVersions = Object.freeze(
      trace.states.filter((r) => r.kind === "checkpoint").map((r) => r.version),
    );
    Object.freeze(this);
  }
  record(version: number): Immutable<StateRecord> {
    const record = this.trace.states[version];
    integrity(
      record !== undefined && record.version === version,
      `Missing state version ${version}`,
    );
    return record;
  }
  event(index: number): Immutable<TraceEvent> {
    const event = this.trace.events[index];
    integrity(
      event !== undefined && event.index === index,
      `Missing event index ${index}`,
    );
    return event;
  }
  checkpoint(version: number): number {
    let left = 0,
      right = this.checkpointVersions.length;
    while (left < right) {
      const middle = Math.floor((left + right) / 2);
      if (this.checkpointVersions[middle]! <= version) left = middle + 1;
      else right = middle;
    }
    const result = this.checkpointVersions[left - 1];
    integrity(
      result !== undefined,
      `No checkpoint for state version ${version}`,
    );
    return result;
  }
}
