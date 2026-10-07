import { useId } from "react";
import type { GraphViewModel } from "../types.ts";
export function GraphVisualizer({ model }: { model: GraphViewModel }) {
  const arrow = useId().replace(/:/g, ""),
    vertices = new Map(model.vertices.map((v) => [v.id, v]));
  return (
    <div>
      <p className="viz-caption">
        Directed graph · {model.vertices.length} вершин · {model.edges.length}{" "}
        рёбер
      </p>
      {!model.vertices.length ? (
        <p>Пустой граф.</p>
      ) : (
        <div className="viz-scroll">
          <svg
            role="img"
            aria-label="Ориентированный граф"
            width={model.size}
            height={model.size}
          >
            <defs>
              <marker
                id={arrow}
                markerWidth={8}
                markerHeight={8}
                refX={7}
                refY={4}
                orient="auto"
              >
                <path d="M 0 0 L 8 4 L 0 8 z" className="viz-arrow" />
              </marker>
            </defs>
            {model.edges.map((e) => {
              const a = vertices.get(e.from)!,
                b = vertices.get(e.to)!,
                dx = b.x - a.x,
                dy = b.y - a.y,
                d = Math.hypot(dx, dy),
                ux = d ? dx / d : 0,
                uy = d ? dy / d : 0;
              const path = e.selfLoop
                ? `M ${a.x - 12} ${a.y - 20} C ${a.x - 65} ${a.y - 80}, ${a.x + 65} ${a.y - 80}, ${a.x + 12} ${a.y - 20}`
                : `M ${a.x + ux * 24} ${a.y + uy * 24} Q ${(a.x + b.x) / 2 - uy * 15} ${(a.y + b.y) / 2 + ux * 15} ${b.x - ux * 28} ${b.y - uy * 28}`;
              return (
                <path
                  key={JSON.stringify([e.from, e.to])}
                  className="viz-edge"
                  d={path}
                  fill="none"
                  markerEnd={`url(#${arrow})`}
                  data-self-loop={e.selfLoop}
                />
              );
            })}
            {model.vertices.map((v) => (
              <g
                key={v.id}
                className={`viz-node${v.current ? " viz-current" : ""}${v.visited ? " viz-visited" : ""}`}
                data-vertex={v.id}
              >
                <title>
                  {v.label +
                    (v.current ? " · current" : "") +
                    (v.visited ? " · visited" : "")}
                </title>
                <circle cx={v.x} cy={v.y} r={24} />
                <text x={v.x} y={v.y + 4} textAnchor="middle">
                  {v.label.length > 9 ? v.label.slice(0, 8) + "…" : v.label}
                </text>
                <text
                  className="viz-id"
                  x={v.x}
                  y={v.y + 39}
                  textAnchor="middle"
                >
                  {v.current ? "C " : ""}
                  {v.visited ? "V" : ""}
                </text>
              </g>
            ))}
          </svg>
        </div>
      )}
      {model.queue.length > 0 && (
        <p className="viz-note">
          Queue:{" "}
          {model.queue
            .slice(0, 20)
            .map((id) => vertices.get(id)!.label.slice(0, 80))
            .join(" → ")}
          {model.queue.length > 20 && ` · ещё ${model.queue.length - 20}`}
        </p>
      )}
      {model.stack.length > 0 && (
        <p className="viz-note">
          Stack:{" "}
          {model.stack
            .slice(0, 20)
            .map((id) => vertices.get(id)!.label.slice(0, 80))
            .join(" → ")}
          {model.stack.length > 20 && ` · ещё ${model.stack.length - 20}`}
        </p>
      )}
      {model.notes.map((n, i) => (
        <p className="viz-note" key={i}>
          {n}
        </p>
      ))}
      <p className="viz-note">
        C — current · V — visited · полные labels доступны в подсказке узла
      </p>
    </div>
  );
}
