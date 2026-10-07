import type { StoredObject, StoredValue } from "../trace/types.ts";

export interface PreviewOptions {
  maxDepth?: number;
  maxItems?: number;
  maxNodes?: number;
}
/** Accepts trusted immutable TRACE DTOs only. Object IDs remain visible at every edge. */
export function previewValue(
  value: StoredValue,
  objects: ReadonlyMap<string, StoredObject>,
  options: PreviewOptions = {},
): string {
  const depthLimit = options.maxDepth ?? 2,
    itemLimit = options.maxItems ?? 4;
  let budget = options.maxNodes ?? 60;
  if (
    ![depthLimit, itemLimit, budget].every(
      (n) => Number.isSafeInteger(n) && n >= 0,
    )
  )
    throw new RangeError("Preview limits must be nonnegative integers");
  const path = new Set<string>();
  const format = (v: StoredValue, depth: number): string => {
    if (!("$ref" in v)) {
      switch (v.type) {
        case "none":
          return "None";
        case "bool":
          return v.value ? "True" : "False";
        case "str":
          return JSON.stringify(v.value);
        default:
          return v.value;
      }
    }
    const object = objects.get(v.$ref),
      identity = v.$ref + " · " + (object?.kind ?? "unavailable");
    if (path.has(v.$ref)) return identity + " ⟳ cycle";
    if (!object) return identity;
    if (object.kind === "opaque")
      return identity + " (" + object.pythonType + "; " + object.reason + ")";
    if (depth >= depthLimit) return identity + " … depth limit";
    if (budget-- <= 0) return identity + " … preview limit";
    path.add(v.$ref);
    const items =
      object.kind === "dict"
        ? object.entries
        : object.kind === "instance"
          ? object.attrs
          : object.items;
    const visible = items.slice(0, itemLimit).map((item) => {
      if ("key" in item)
        return (
          format(item.key, depth + 1) + ": " + format(item.value, depth + 1)
        );
      if ("name" in item)
        return JSON.stringify(item.name) + ": " + format(item.value, depth + 1);
      return format(item, depth + 1);
    });
    if (items.length > itemLimit)
      visible.push("… " + (items.length - itemLimit) + " hidden");
    path.delete(v.$ref);
    const caption =
      object.kind === "instance"
        ? " " + object.className
        : object.kind === "deque"
          ? " maxlen=" + (object.maxlen === null ? "None" : object.maxlen)
          : "";
    return identity + caption + " [" + visible.join(", ") + "]";
  };
  return format(value, 0);
}
