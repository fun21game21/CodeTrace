import type { SelectedStepView } from "../../trace/index.ts";
import { previewValue } from "../value_preview.ts";

export function EventPanel({ view }: { view: SelectedStepView | null }) {
  if (!view)
    return (
      <section className="event-panel">
        <h2>Событие</h2>
        <p className="muted">После выполнения здесь появится выбранный шаг.</p>
      </section>
    );
  const event = view.event,
    frame = view.callStack.find((f) => f.id === event.frameId);
  return (
    <section className="event-panel" aria-labelledby="event-title">
      <h2 id="event-title">Событие · шаг {view.step}</h2>
      <p className="event-kind">{event.kind}</p>
      <dl>
        <dt>State version</dt>
        <dd>{event.stateVersion}</dd>
        <dt>Строка</dt>
        <dd>{event.source?.lineNumber ?? "—"}</dd>
        <dt>Frame</dt>
        <dd>{event.frameId ?? "—"}</dd>
      </dl>
      {event.kind === "LINE" && (
        <p>Перед выполнением строки {event.source.lineNumber}.</p>
      )}
      {event.kind === "RETURN" && (
        <p>
          {event.payload.returnValueKnown
            ? "Возвращаемое значение: " +
              previewValue(
                event.payload.returnValue!,
                new Map(view.state.objects.map((o) => [o.id, o])),
              )
            : frame?.kind === "module"
              ? "Завершение модуля."
              : frame?.kind === "class"
                ? "Выход из области класса."
                : "Выход из функции."}
        </p>
      )}
      {event.kind === "EXCEPTION" && <p>Наблюдаемое исключение.</p>}
      {event.kind === "RUN_END" && <p>Выполнение завершено.</p>}
    </section>
  );
}
