import type { ArrayViewModel } from "../types.ts";
export function ArrayVisualizer({ model }: { model: ArrayViewModel }) {
  const magnitude = Math.max(
      Math.abs(model.scale.min),
      Math.abs(model.scale.max),
    ),
    lo = model.scale.min / magnitude,
    hi = model.scale.max / magnitude;
  const y = (n: number) => 160 - ((n / magnitude - lo) / (hi - lo)) * 140;
  return (
    <div className="array-visualizer">
      <p className="viz-caption">
        {model.binding} · <code>{model.objectId}</code> ·{" "}
        {model.effectiveMode === "cells" ? "Ячейки" : "Столбцы"}
      </p>
      {model.modeReason && <p className="viz-note">{model.modeReason}</p>}
      {model.elements.length === 0 ? (
        <p>Пустой list.</p>
      ) : model.effectiveMode === "cells" ? (
        <ol className="array-cells" aria-label="Ячейки массива">
          {model.elements.map((e) => (
            <li
              key={e.index}
              className={e.changed ? "viz-changed" : ""}
              data-index={e.index}
              data-changed={e.changed}
            >
              <small>
                [{e.index}] {e.changed ? "· изменено" : ""}
              </small>
              <code>{e.label}</code>
              {model.markerIndices
                .filter((m) => m.index === e.index)
                .map((m, i) => (
                  <span className="viz-marker" key={i}>
                    {m.name}
                  </span>
                ))}
            </li>
          ))}
        </ol>
      ) : (
        <div className="viz-scroll">
          <svg
            role="img"
            aria-label="Столбцы массива"
            width={Math.max(240, model.elements.length * 60)}
            height={220}
          >
            <line
              className="bar-baseline"
              x1={0}
              x2={Math.max(240, model.elements.length * 60)}
              y1={y(0)}
              y2={y(0)}
            />
            {model.elements.map((e) => (
              <g key={e.index} data-index={e.index} data-changed={e.changed}>
                <title>{`[${e.index}] ${e.label}${e.changed ? " · изменено" : ""}`}</title>
                <rect
                  className={e.changed ? "bar viz-changed" : "bar"}
                  x={e.index * 60 + 8}
                  y={Math.min(y(0), y(e.numeric!))}
                  width={36}
                  height={Math.max(1, Math.abs(y(e.numeric!) - y(0)))}
                />
                <text x={e.index * 60 + 26} y={185} textAnchor="middle">
                  {e.label.length > 9 ? e.label.slice(0, 8) + "…" : e.label}
                </text>
                <text x={e.index * 60 + 26} y={204} textAnchor="middle">
                  [{e.index}]{e.changed ? " *" : ""}
                </text>
              </g>
            ))}
          </svg>
        </div>
      )}
      {model.hiddenCount > 0 && (
        <p className="viz-note">
          Скрыто элементов: {model.hiddenCount}. Показаны первые 200.
        </p>
      )}
      {model.markerIndices.length > 0 && (
        <p className="viz-note">
          Маркеры:{" "}
          {model.markerIndices
            .map(
              (m) =>
                `${m.name}=${m.indexText}${m.outsideVisible ? " (вне видимой области)" : ""}`,
            )
            .join("; ")}
        </p>
      )}
      {model.notes.map((n, i) => (
        <p className="viz-note" key={i}>
          {n}
        </p>
      ))}
    </div>
  );
}
