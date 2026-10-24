// Inline picker (replaces Decky's DropdownItem, which opens Steam's separate popup menu window): a full-width row
// "Label  Value ▾" that expands its options as full-width rows underneath, inside the panel. Pure state rules here,
// so `node --test` can load this file directly (no imports, only erasable TypeScript, like curve.ts).

export type PickerOption<T> = { data: T; label: string };

// Label of the current value; '' when the value is not one of the options.
export function valueLabel<T>(options: PickerOption<T>[], value: T): string {
  const o = options.find((x) => x.data === value);
  return o ? o.label : '';
}

// The row's right-hand text: the current value and a chevron that points the way the list will go.
export function rowValue<T>(options: PickerOption<T>[], value: T, open: boolean): string {
  const v = valueLabel(options, value);
  return (v ? v + ' ' : '') + (open ? '▴' : '▾');
}

// Pressing the row (A, tap or click) opens the list, or closes it without a change. Disabled: always closed.
export function pressRow(open: boolean, disabled = false): boolean {
  return disabled ? false : !open;
}

// Choosing an option collapses the list; apply is false when it is the value already in use (nothing to send).
export function choose<T>(current: T, picked: T): { open: boolean; apply: boolean } {
  return { open: false, apply: picked !== current };
}

// B collapses an open list without a change. handled=false while closed: B then does what it normally does.
export function pressCancel(open: boolean): { open: boolean; handled: boolean } {
  return { open: false, handled: open };
}

// Where controller focus goes: the current option while open (the first one when the value is not listed),
// -1 (the row) while closed.
export function focusIndex<T>(options: PickerOption<T>[], value: T, open: boolean): number {
  if (!open || options.length === 0) return -1;
  const i = options.findIndex((x) => x.data === value);
  return i >= 0 ? i : 0;
}
