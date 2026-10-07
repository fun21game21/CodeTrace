import type { StoredValue, StoredFrame } from "../trace/types.ts";
export interface BindingSelector {
  readonly scope: "globals" | "current_frame";
  readonly name: string;
}
export type VisualizerConfig =
  | { readonly kind: "none" }
  | {
      readonly kind: "array";
      readonly array?: BindingSelector;
      readonly mode: "cells" | "bars";
      readonly indices: readonly BindingSelector[];
    }
  | {
      readonly kind: "tree";
      readonly root: BindingSelector;
      readonly valueField: string;
      readonly leftField: string;
      readonly rightField: string;
      readonly current?: BindingSelector;
      readonly visited?: BindingSelector;
    }
  | {
      readonly kind: "graph";
      readonly adjacency: BindingSelector;
      readonly current?: BindingSelector;
      readonly visited?: BindingSelector;
      readonly queue?: BindingSelector;
      readonly stack?: BindingSelector;
    }
  | { readonly kind: "call_stack" };
export type VisualizerKind = VisualizerConfig["kind"];
export interface VisualizerUnavailable {
  readonly available: false;
  readonly code:
    | "INVALID_CONFIG"
    | "MISSING_BINDING"
    | "WRONG_TYPE"
    | "MISSING_FIELD"
    | "CYCLE"
    | "SHARED_CHILD"
    | "LIMIT";
  readonly reason: string;
}
export const unavailable = (
  code: VisualizerUnavailable["code"],
  reason: string,
): VisualizerUnavailable => Object.freeze({ available: false, code, reason });
export interface ArrayViewModel {
  readonly available: true;
  readonly kind: "array";
  readonly objectId: string;
  readonly binding: string;
  readonly elements: readonly {
    readonly index: number;
    readonly value: StoredValue;
    readonly label: string;
    readonly changed: boolean;
    readonly numeric: number | null;
  }[];
  readonly markerIndices: readonly {
    readonly name: string;
    readonly indexText: string;
    readonly index: number | null;
    readonly outsideVisible: boolean;
  }[];
  readonly hiddenCount: number;
  readonly effectiveMode: "cells" | "bars";
  readonly modeReason?: string;
  readonly scale: { readonly min: number; readonly max: number };
  readonly notes: readonly string[];
}
export interface TreeViewModel {
  readonly available: true;
  readonly kind: "tree";
  readonly rootId: string | null;
  readonly nodes: readonly {
    readonly id: string;
    readonly label: string;
    readonly x: number;
    readonly y: number;
    readonly current: boolean;
    readonly visited: boolean;
  }[];
  readonly edges: readonly {
    readonly from: string;
    readonly to: string;
    readonly side: "left" | "right";
  }[];
  readonly currentId: string | null;
  readonly visitedIds: readonly string[];
  readonly notes: readonly string[];
  readonly width: number;
  readonly height: number;
}
export interface VertexKey {
  readonly type: "int" | "str";
  readonly value: string;
}
export interface GraphViewModel {
  readonly available: true;
  readonly kind: "graph";
  readonly vertices: readonly {
    readonly key: VertexKey;
    readonly id: string;
    readonly label: string;
    readonly x: number;
    readonly y: number;
    readonly current: boolean;
    readonly visited: boolean;
  }[];
  readonly edges: readonly {
    readonly from: string;
    readonly to: string;
    readonly selfLoop: boolean;
  }[];
  readonly queue: readonly string[];
  readonly stack: readonly string[];
  readonly notes: readonly string[];
  readonly size: number;
}
export interface CallStackViewModel {
  readonly available: true;
  readonly kind: "call_stack";
  readonly frames: readonly {
    readonly id: string;
    readonly kind: StoredFrame["kind"];
    readonly name: string;
    readonly selected: boolean;
    readonly locals: StoredFrame["locals"];
    readonly callSite: StoredFrame["callSite"];
    readonly returning: boolean;
    readonly returnLabel?: string;
  }[];
}
export type VisualizerViewModel =
  | ArrayViewModel
  | TreeViewModel
  | GraphViewModel
  | CallStackViewModel
  | { readonly available: true; readonly kind: "none" };
export type VisualizerResult = VisualizerViewModel | VisualizerUnavailable;
