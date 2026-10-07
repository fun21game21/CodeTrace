import type { SelectedStepView } from "../trace/index.ts";
import type { StoredValue } from "../trace/types.ts";
import type { BindingSelector } from "./types.ts";
import { nameOrder } from "../trace/validation.ts";
export { nameOrder };
export function validSelector(value: unknown): value is BindingSelector {
  if (!value || typeof value !== "object") return false;
  const proto = Object.getPrototypeOf(value);
  if (proto !== Object.prototype && proto !== null) return false;
  if (!Object.hasOwn(value, "scope") || !Object.hasOwn(value, "name"))
    return false;
  if (
    !Reflect.ownKeys(value).every(
      (k) =>
        typeof k === "string" &&
        "value" in Object.getOwnPropertyDescriptor(value, k)!,
    )
  )
    return false;
  const s = value as BindingSelector;
  return (
    (s.scope === "globals" || s.scope === "current_frame") &&
    typeof s.name === "string" &&
    /^[\p{ID_Start}_][\p{ID_Continue}_]*$/u.test(s.name)
  );
}
export function lookup(
  view: SelectedStepView,
  selector: BindingSelector,
): StoredValue | undefined {
  const top = view.callStack.at(-1);
  const bindings =
    selector.scope === "globals"
      ? view.globals
      : !top
        ? []
        : top.localsSource === "globals"
          ? view.globals
          : top.locals;
  return bindings.find((b) => b.name === selector.name)?.value;
}
