// Pure helpers for the "tap to move" arrangement on the plugin list.
// Kept free of imports and non-erasable TypeScript so `node --test` can load it directly.

/**
 * Returns a new order with `name` moved into the gap `toIndex`.
 * Gaps are numbered against the current order: gap 0 is above the first entry,
 * gap k is just above `order[k]`, and gap `order.length` is below the last entry.
 * An unknown name or an out-of-range gap leaves the order unchanged (as a copy).
 */
export function moveEntry(order: string[], name: string, toIndex: number): string[] {
  const from = order.indexOf(name);
  if (from < 0 || !Number.isInteger(toIndex) || toIndex < 0 || toIndex > order.length) {
    return [...order];
  }
  const result = order.filter((_, i) => i !== from);
  const insertAt = toIndex > from ? toIndex - 1 : toIndex;
  result.splice(insertAt, 0, name);
  return result;
}

/**
 * The gaps worth offering as "Move here" targets for the entry at `fromIndex`
 * in a list of `length` entries: every gap except the two touching the entry,
 * since dropping it there would not change anything.
 */
export function moveTargets(length: number, fromIndex: number): number[] {
  const targets: number[] = [];
  for (let gap = 0; gap <= length; gap++) {
    if (gap !== fromIndex && gap !== fromIndex + 1) targets.push(gap);
  }
  return targets;
}
