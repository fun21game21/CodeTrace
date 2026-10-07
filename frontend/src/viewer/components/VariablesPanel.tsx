import type { SelectedStepView } from "../../trace/index.ts";
import type { Bindings } from "../../trace/types.ts";
import { previewValue } from "../value_preview.ts";

export function VariablesPanel({ view }: { view: SelectedStepView }) {
  const objects = new Map(
    view.state.objects.map((object) => [object.id, object]),
  );
  const changed = new Set(view.changedBindingNames);
  const frame = view.callStack.find(
    (entry) => entry.id === view.selectedFrameId,
  );
  const rows = (bindings: Bindings) =>
    bindings.length === 0 ? (
      <p className="muted">Нет переменных в этой области.</p>
    ) : (
      <table className="variables-table">
        <thead>
          <tr>
            <th>Имя</th>
            <th>Значение</th>
          </tr>
        </thead>
        <tbody>
          {bindings.map((binding) => (
            <tr
              key={binding.name}
              className={
                changed.has(binding.name) ? "binding-changed" : undefined
              }
            >
              <th scope="row">
                <code>{binding.name}</code>
                {changed.has(binding.name) && (
                  <span
                    className="changed-mark"
                    aria-label="Изменено относительно предыдущего события"
                  >
                    изменено
                  </span>
                )}
              </th>
              <td>
                <code>{previewValue(binding.value, objects)}</code>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    );
  return (
    <section className="variables-panel" aria-labelledby="variables-title">
      <h2 id="variables-title">Переменные</h2>
      <h3>Globals</h3>
      {rows(view.globals)}
      {frame?.localsSource === "own" && (
        <>
          <h3>
            Locals — {frame.name} · {frame.id}
          </h3>
          {rows(view.selectedLocals)}
        </>
      )}
    </section>
  );
}
