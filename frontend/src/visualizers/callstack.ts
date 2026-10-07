import type { SelectedStepView } from "../trace/index.ts";
import type { CallStackViewModel } from "./types.ts";
import { formatValue } from "./value_format.ts";
export function callStackAdapter(view: SelectedStepView): CallStackViewModel {
  const objects = new Map(view.state.objects.map((o) => [o.id, o]));
  return {
    available: true,
    kind: "call_stack",
    frames: view.callStack.map((f) => {
      const returning =
        view.event.kind === "RETURN" && view.event.frameId === f.id;
      return {
        id: f.id,
        kind: f.kind,
        name: f.name,
        selected: f.id === view.selectedFrameId,
        locals: f.localsSource === "globals" ? view.globals : f.locals,
        callSite: f.callSite,
        returning,
        ...(returning && view.event.kind === "RETURN"
          ? {
              returnLabel: view.event.payload.returnValueKnown
                ? formatValue(view.event.payload.returnValue, objects)
                : "выход из функции",
            }
          : {}),
      };
    }),
  };
}
