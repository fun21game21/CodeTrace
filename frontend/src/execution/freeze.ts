/** Restore structured-cloned DTO immutability without recursive JS stack growth. */
export function freezeView<T>(value: T): T {
  const pending: unknown[] = [value],
    seen = new Set<object>();
  let nodes = 0;
  while (pending.length) {
    const item = pending.pop();
    if (item === null || typeof item !== "object" || seen.has(item)) continue;
    if (++nodes > 500000) throw Error("Selected view exceeds traversal budget");
    seen.add(item);
    for (const child of Object.values(item))
      if (child !== null && typeof child === "object") pending.push(child);
    Object.freeze(item);
  }
  return value;
}
