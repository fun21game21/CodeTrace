import type { TreeViewModel } from "../types.ts";
export function TreeVisualizer({ model }: { model: TreeViewModel }) {
  const nodes = new Map(model.nodes.map((n) => [n.id, n]));
  return (
    <div>
      <p className="viz-caption">Binary tree · {model.nodes.length} узлов</p>
      {!model.nodes.length ? (
        <p>Пустое дерево.</p>
      ) : (
        <div className="viz-scroll">
          <svg
            role="img"
            aria-label="Бинарное дерево"
            width={model.width}
            height={model.height}
          >
            {model.edges.map((e) => {
              const a = nodes.get(e.from)!,
                b = nodes.get(e.to)!;
              return (
                <g key={e.from + e.side}>
                  <line
                    className="viz-edge"
                    x1={a.x}
                    y1={a.y + 20}
                    x2={b.x}
                    y2={b.y - 20}
                  />
                  <text x={(a.x + b.x) / 2} y={(a.y + b.y) / 2}>
                    {e.side === "left" ? "L" : "R"}
                  </text>
                </g>
              );
            })}
            {model.nodes.map((n) => (
              <g
                key={n.id}
                data-node={n.id}
                className={`viz-node${n.current ? " viz-current" : ""}${n.visited ? " viz-visited" : ""}`}
              >
                <title>
                  {n.id +
                    ": " +
                    n.label +
                    (n.current ? " · current" : "") +
                    (n.visited ? " · visited" : "")}
                </title>
                <rect x={n.x - 42} y={n.y - 20} width={84} height={40} rx={4} />
                <text x={n.x} y={n.y + 4} textAnchor="middle">
                  {n.label.length > 10 ? n.label.slice(0, 9) + "…" : n.label}
                </text>
                <text
                  className="viz-id"
                  x={n.x}
                  y={n.y + 36}
                  textAnchor="middle"
                >
                  {n.id}
                  {n.current ? " C" : ""}
                  {n.visited ? " V" : ""}
                </text>
              </g>
            ))}
          </svg>
        </div>
      )}
      {model.notes.map((n, i) => (
        <p className="viz-note" key={i}>
          {n}
        </p>
      ))}
      <p className="viz-note">
        L — left · R — right · C — current · V — visited
      </p>
    </div>
  );
}
