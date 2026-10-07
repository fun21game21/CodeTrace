export function graphLayout(ids: readonly string[]) {
  const size = Math.max(300, Math.ceil(ids.length * 12)),
    radius = (size - 100) / 2;
  return {
    size,
    positions: new Map(
      ids.map((id, i) => [
        id,
        {
          x:
            size / 2 +
            radius * Math.cos(-Math.PI / 2 + (2 * Math.PI * i) / ids.length),
          y:
            size / 2 +
            radius * Math.sin(-Math.PI / 2 + (2 * Math.PI * i) / ids.length),
        },
      ]),
    ),
  };
}
