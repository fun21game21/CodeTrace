import type { CallStackViewModel } from "../types.ts";
export function CallStackVisualizer({
  model,
  onFrame,
}: {
  model: CallStackViewModel;
  onFrame?: (id: string | null) => void;
}) {
  return (
    <div>
      <p className="viz-caption">Активные frames · {model.frames.length}</p>
      <button onClick={() => onFrame?.(null)}>Globals</button>
      {model.frames.length === 0 ? (
        <p>Активных кадров нет.</p>
      ) : (
        <ol className="viz-stack">
          {model.frames.map((f) => (
            <li key={f.id}>
              <button aria-pressed={f.selected} onClick={() => onFrame?.(f.id)}>
                <strong>
                  {f.name} · {f.id}
                </strong>
                <span>
                  {f.kind} · {f.locals.length} bindings
                  {f.callSite?.lineNumber
                    ? " · вызов на строке " + f.callSite.lineNumber
                    : ""}
                </span>
                {f.returning && <span>RETURN · {f.returnLabel}</span>}
              </button>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
