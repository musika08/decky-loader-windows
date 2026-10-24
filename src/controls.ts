// Pure text and rules for the Traction panel (no imports, only erasable TypeScript, so `node --test` loads it
// straight from src). Ported from Overdrive 0.1.0's curve.ts, trimmed to the rows Traction has.

export type ControllerMode = 'gamepad' | 'desktop' | 'auto';

export const ARMOURY_LABEL = 'Use Traction instead of Armoury Crate';
export const ARMOURY_DESCRIPTION =
  'Stops the Armoury Crate services so they do not undo these settings. Turning it off restores them.';
export const ARMOURY_BUSY = 'Switching Armoury Crate services, this can take a few minutes';
export const GRIP_VIBRATION_DESCRIPTION = 'Vibration motors in the grips.';
export const TOUCHSCREEN_DESCRIPTION = 'Turns the touch screen off, e.g. to avoid stray touches.';
export const CONTROLLER_NOT_FOUND = 'Controller not found';
export const NOT_ALLY = 'Traction supports the ROG Ally family only';

export const CONTROLLER_OPTIONS: { data: ControllerMode; label: string }[] = [
  { data: 'gamepad', label: 'Gamepad' },
  { data: 'desktop', label: 'Desktop' },
  { data: 'auto', label: 'Auto' },
];

export function controllerModeDescription(mode: string | null | undefined): string {
  if (mode === 'desktop') return 'Sticks and triggers work as a mouse; Steam menus cannot be steered.';
  if (mode === 'auto') return 'Gamepad in Steam and games, mouse on the desktop.';
  return 'The controller works as a gamepad (Steam and games).';
}

// Touchscreen change: the helper replies at once, so the panel polls the device status until it matches the target.
export const TOUCH_POLL_MS = 1500;
export const TOUCH_TIMEOUT_MS = 15000;
export const TOUCH_TIMEOUT_TEXT = 'Touch screen did not respond';

export function touchscreenCheck(enabled: boolean | undefined, target: boolean, elapsedMs: number): 'done' | 'wait' | 'timeout' {
  if (enabled === target) return 'done';
  return elapsedMs >= TOUCH_TIMEOUT_MS ? 'timeout' : 'wait';
}

// The line above the controller rows: `blocked` (rows disabled, shown red) wins over `note` (shown plain).
export function controllerBanner(
  blocked: string | undefined,
  note: string | undefined,
): { text: string; color: string; disabled: boolean } {
  if (blocked) return { text: blocked, color: '#ff6b6b', disabled: true };
  if (note) return { text: note, color: '#dcdedf', disabled: false };
  return { text: '', color: '#dcdedf', disabled: false };
}

// Steam's toggle flips itself on every press, so it is fed what it should show: while Armoury services switch, the
// target (the opposite of the stored state); else the stored state.
export function armouryChecked(replaced: boolean, busy: boolean): boolean {
  return busy ? !replaced : replaced;
}
