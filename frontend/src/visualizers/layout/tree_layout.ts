export function treeLayout(
  root: string | null,
  edges: readonly { from: string; to: string; side: "left" | "right" }[],
) {
  const children = new Map<string, { left?: string; right?: string }>();
  for (const e of edges) {
    let c = children.get(e.from);
    if (!c) {
      c = {};
      children.set(e.from, c);
    }
    c[e.side] = e.to;
  }
  const positions = new Map<string, { x: number; y: number }>();
  let rank = 0,
    depthMax = 0;
  const pending: { id: string; depth: number; visit: boolean }[] = root
    ? [{ id: root, depth: 0, visit: false }]
    : [];
  while (pending.length) {
    const t = pending.pop()!,
      c = children.get(t.id);
    if (t.visit) {
      positions.set(t.id, { x: 60 + rank++ * 100, y: 50 + t.depth * 90 });
      depthMax = Math.max(depthMax, t.depth);
      continue;
    }
    if (c?.right)
      pending.push({ id: c.right, depth: t.depth + 1, visit: false });
    pending.push({ ...t, visit: true });
    if (c?.left) pending.push({ id: c.left, depth: t.depth + 1, visit: false });
  }
  return {
    positions,
    width: Math.max(200, rank * 100 + 20),
    height: Math.max(140, depthMax * 90 + 100),
  };
}
