import type { StoredObject, StoredValue } from "../trace/types.ts";
/** Trusted immutable DTOs only. No live Python access or user formatting hooks. */
export function formatValue(
  value: StoredValue,
  objects: ReadonlyMap<string, StoredObject>,
): string {
  const path = new Set<string>();
  let budget = 40;
  const format = (v: StoredValue, depth: number): string => {
    if (!("$ref" in v)) {
      if (v.type === "none") return "None";
      if (v.type === "bool") return v.value ? "True" : "False";
      if (v.type === "str") {
        const text = JSON.stringify(v.value.slice(0, 120));
        return text + (v.value.length > 120 ? " … (обрезано)" : "");
      }
      return v.value;
    }
    const o = objects.get(v.$ref),
      prefix = v.$ref + " · " + (o?.kind ?? "ref");
    if (path.has(v.$ref)) return prefix + " (cycle)";
    if (!o || depth >= 2 || budget-- <= 0) return prefix + " (…)";
    if (o.kind === "opaque")
      return prefix + " (" + o.pythonType.slice(0, 80) + ")";
    path.add(v.$ref);
    const values =
      o.kind === "dict"
        ? o.entries
            .slice(0, 3)
            .map(
              (e) =>
                format(e.key, depth + 1) + ": " + format(e.value, depth + 1),
            )
        : o.kind === "instance"
          ? o.attrs
              .slice(0, 3)
              .map(
                (b) => b.name.slice(0, 40) + "=" + format(b.value, depth + 1),
              )
          : o.items.slice(0, 3).map((x) => format(x, depth + 1));
    const count =
      o.kind === "dict"
        ? o.entries.length
        : o.kind === "instance"
          ? o.attrs.length
          : o.items.length;
    path.delete(v.$ref);
    const text =
      prefix + " [" + values.join(", ") + (count > 3 ? ", …" : "") + "]";
    return text.length > 240 ? text.slice(0, 240) + " … (обрезано)" : text;
  };
  return format(value, 0);
}
export function numeric(value: StoredValue): number | null {
  if ("$ref" in value || (value.type !== "int" && value.type !== "float"))
    return null;
  const n = Number(value.value);
  return Number.isFinite(n) && (value.type !== "int" || Number.isSafeInteger(n))
    ? n
    : null;
}
