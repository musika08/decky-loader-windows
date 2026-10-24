// Pure helpers for the panel. No imports, only erasable TypeScript, so `node --test` can load this file directly.
// validateCurve copies helper\src\Validation.cs CheckCurve, reason strings included (ruling P1); change both together.

export const CURVE_POINTS = 8;
export const CURVE_TEMP_MIN = 20;
export const CURVE_TEMP_MAX = 110;
export const CURVE_SPEED_MAX = 100;

export type Curve = { temps: number[]; speeds: number[] };

// Lowest fan percentage G-Helper's editor allows at this temperature: ceil((temp - 70) * 1.2), at least 0.
export function minSpeedAt(temp: number): number {
  const tenths = (temp - 70) * 12;
  if (tenths <= 0) return 0;
  return Math.floor((tenths + 9) / 10);
}

export function validateCurve(temps: number[], speeds: number[]): string | null {
  if (!Array.isArray(temps) || !Array.isArray(speeds) || temps.length !== CURVE_POINTS || speeds.length !== CURVE_POINTS) {
    return 'The fan curve needs 8 points.';
  }
  for (let i = 0; i < CURVE_POINTS; i++) {
    const n = i + 1;
    // The helper takes C# int[]; NaN, undefined or fractions can never be sent (not a CheckCurve message).
    if (!Number.isInteger(temps[i]) || !Number.isInteger(speeds[i])) {
      return 'Point ' + n + ': temperature and fan speed must be whole numbers.';
    }
    if (temps[i] < CURVE_TEMP_MIN || temps[i] > CURVE_TEMP_MAX) {
      return 'Point ' + n + ': temperature must be between ' + CURVE_TEMP_MIN + ' and ' + CURVE_TEMP_MAX + ' °C.';
    }
    if (speeds[i] < 0 || speeds[i] > CURVE_SPEED_MAX) {
      return 'Point ' + n + ': fan speed must be between 0 and ' + CURVE_SPEED_MAX + ' %.';
    }
    // Firmware curves repeat their last temperature: non-decreasing temps, one speed per temperature.
    if (i > 0 && temps[i] < temps[i - 1]) {
      return 'Point ' + n + ": temperature can't be lower than point " + i + '.';
    }
    if (i > 0 && speeds[i] < speeds[i - 1]) {
      return 'Point ' + n + ": fan speed can't be lower than point " + i + '.';
    }
    if (i > 0 && temps[i] === temps[i - 1] && speeds[i] !== speeds[i - 1]) {
      return 'Point ' + n + ': same temperature as point ' + i + ', so the fan speed must be the same.';
    }
    const min = minSpeedAt(temps[i]);
    if (speeds[i] < min) {
      return 'Point ' + n + ': at ' + temps[i] + ' °C the fan must be at least ' + min + ' %.';
    }
  }
  return null;
}

// Fan presets in the panel's order (settings.py PRESETS holds the same names).
export type Preset = 'default' | 'recommended' | 'quiet' | 'cool' | 'custom';
export const FAN_PRESETS: { data: Preset; label: string }[] = [
  { data: 'default', label: 'Armoury default' },
  { data: 'recommended', label: 'Recommended' },
  { data: 'quiet', label: 'Quiet' },
  { data: 'cool', label: 'Cool' },
  { data: 'custom', label: 'Custom' },
];

// The Recommended preset: one fixed curve for both fans, owner-approved 2026-10-23. Mirrors RECOMMENDED_CURVE in
// py_modules/overdrive/settings.py (the backend sends it; the panel only shows what get_state returns).
export const RECOMMENDED_CURVE: Curve = {
  temps: [40, 50, 60, 65, 70, 75, 80, 90],
  speeds: [10, 15, 25, 35, 45, 55, 70, 90],
};

// Starting point for the editor when the backend has no curve yet (device default curve not read).
export function defaultDraft(): Curve {
  return { temps: [30, 40, 50, 60, 70, 80, 90, 100], speeds: [0, 10, 20, 30, 40, 50, 60, 70] };
}

export function stepValue(value: number, delta: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, Math.round(value + delta)));
}

function show(v: number | null | undefined): string {
  return typeof v === 'number' && isFinite(v) ? String(Math.round(v)) : '--';
}

export type LiveSensors = {
  cpuTempC?: number | null;
  fanRpm?: number | null;
  cpuUse?: number | null;
  cpuPowerW?: number | null;
  gpuTempC?: number | null;
  gpuFanRpm?: number | null;
  gpuUse?: number | null;
  gpuPowerW?: number | null;
};

function known(v: number | null | undefined): v is number {
  return typeof v === 'number' && isFinite(v);
}

// One live line: "CPU 51 °C · 3300 RPM · 23 % · 8 W". A value that is not reported is left out (the rest keep their
// order); null when the line has no values at all.
function liveLineOf(
  name: string,
  tempC: number | null | undefined,
  rpm: number | null | undefined,
  use: number | null | undefined,
  powerW: number | null | undefined,
): string | null {
  const parts: string[] = [];
  if (known(tempC)) parts.push(show(tempC) + ' °C');
  if (known(rpm)) parts.push(show(rpm) + ' RPM');
  if (known(use)) parts.push(show(use) + ' %');
  if (known(powerW)) parts.push(show(powerW) + ' W');
  return parts.length ? name + ' ' + parts.join(' · ') : null;
}

// The live readout: the CPU line, then the GPU line below it, each "temperature · fan RPM · usage % · power W".
// A line with no values is not shown.
export function liveLines(s: LiveSensors): string[] {
  const lines = [
    liveLineOf('CPU', s.cpuTempC, s.fanRpm, s.cpuUse, s.cpuPowerW),
    liveLineOf('GPU', s.gpuTempC, s.gpuFanRpm, s.gpuUse, s.gpuPowerW),
  ];
  return lines.filter((l): l is string => l !== null);
}

// ---------- mode and power display (probe follow-ups) ----------

export type DeviceMode = 'silent' | 'balanced' | 'turbo' | 'other' | null;
export type ShownMode = 'silent' | 'balanced' | 'turbo' | 'manual' | 'other' | null;

// Which mode button the panel highlights. A device mode Overdrive does not set ("other", e.g. Armoury Crate's own)
// always wins, as nothing else is true then; before the owner changed anything the device's mode is shown (the
// stored default is only a default); after that the stored mode (Manual reads back as Balanced on the device).
export function shownMode(stored: string, userSet: boolean | undefined, device: DeviceMode | undefined): ShownMode {
  // Once the user has chosen a mode, show it: the Ally always reads back 7 ("other"), so the device value would
  // otherwise hide every choice. Before that, show what the device runs.
  if (userSet !== false) return stored as ShownMode;
  return device ?? null;
}

export const MODE_OTHER_TEXT = 'Set by Armoury Crate';
export const FIRMWARE_DEFAULT_TEXT = 'Firmware default';

// Watts each mode sends as all three limits; mirrors py_modules/overdrive/settings.py MODE_POWER.
export const MODE_POWER: Record<'silent' | 'balanced' | 'turbo', number> = { silent: 13, balanced: 17, turbo: 35 };

export function modePowerLine(mode: string | null | undefined, autoOn = false): string | null {
  if (mode !== 'silent' && mode !== 'balanced' && mode !== 'turbo') return null;
  return autoOn ? 'Power limit: up to ' + MODE_POWER[mode] + ' W (Auto TDP)' : 'Power limit: ' + MODE_POWER[mode] + ' W';
}

// ---------- Auto TDP ----------

export const AUTO_TDP_UNSUPPORTED = "Auto TDP needs the AMD driver's frame-rate reading, which this driver does not offer.";

export function autoTdpDescription(fpsLimit: number): string {
  if (!fpsLimit) return 'FPS limit Off: the most frames, trimming only watts that add none.';
  return 'Holds ' + fpsLimit + ' FPS at the lowest watts.';
}

// The live line while Auto TDP is on; a pause reason replaces it. A null or 0 fps shows "--".
export function autoTdpLine(s: { watts: number | null; fps: number | null; best: number | null; paused: string }): string {
  if (s.paused) return s.paused;
  const fps = known(s.fps) && s.fps > 0 ? show(s.fps) : '--';
  const best = known(s.best) ? ' (best ' + show(s.best) + ')' : '';
  return 'Auto TDP: ' + show(s.watts) + ' W · ' + fps + ' FPS' + best;
}

// The line under the switch from a sensors reply: the live line while the loop runs, and the device's message once a
// refusal stopped it (the loop is off then but the stored switch stays on). Null when there is nothing to show.
export function autoTdpLiveLine(s: { on: boolean; watts: number | null; fps: number | null; best: number | null; paused: string } | null | undefined): string | null {
  if (!s || !(s.on || s.paused)) return null;
  return autoTdpLine(s);
}

// The switch can always be turned off; it can only be turned on when the driver offers the frame-rate reading.
export function autoTdpToggleDisabled(supported: boolean, on: boolean): boolean {
  return !supported && !on;
}

// ---------- CPU Boost, Battery performance, controller ----------

export type PowerMode = 'eco' | 'balanced' | 'best' | 'other';
export type ControllerMode = 'gamepad' | 'desktop' | 'auto';

export const CPU_BOOST_DESCRIPTION = 'Lets the CPU run above its base clock. Off: cooler and longer battery, a little slower.';
export const POWER_MODE_DESCRIPTION = 'Windows power mode for the power source in use now (battery or plugged in).';
export const GRIP_VIBRATION_DESCRIPTION = 'Vibration motors in the grips.';
export const TOUCHSCREEN_DESCRIPTION = 'Turns the touch screen off, e.g. to avoid stray touches.';
export const AUTO_SR_DESCRIPTION = 'Windows Automatic Super Resolution for games started from now on (per-game choice stays in Game Bar).';
export const CONTROLLER_NOT_FOUND = 'Controller not found';

export function powerModeLabel(value: string | null | undefined): string {
  if (value === 'eco') return 'Eco';
  if (value === 'balanced') return 'Balanced';
  if (value === 'best') return 'Best performance';
  return 'Set in Windows';
}

export function controllerModeDescription(mode: string | null | undefined): string {
  if (mode === 'desktop') return 'Sticks and triggers work as a mouse; Steam menus cannot be steered.';
  if (mode === 'auto') return 'Gamepad in Steam and games, mouse on the desktop.';
  return 'The controller works as a gamepad (Steam and games).';
}

// Power limits line outside Manual: the firmware's limits apply. The device reads 0 (not reported) on the Ally,
// so watts are only added when it reports real values.
export function powerLine(spl: number | null | undefined, sppt: number | null | undefined, fppt: number | null | undefined): string {
  const ok = (v: number | null | undefined) => typeof v === 'number' && isFinite(v) && v > 0;
  if (ok(spl) && ok(sppt) && ok(fppt)) return 'Power limits: ' + FIRMWARE_DEFAULT_TEXT + ' (' + spl + ' / ' + sppt + ' / ' + fppt + ' W)';
  return 'Power limits: ' + FIRMWARE_DEFAULT_TEXT;
}

export const AUTO_SR_UNSET = 'Set in Windows';

// Touchscreen change: the helper replies at once, so the panel polls the device status until it matches the target.
export const TOUCH_POLL_MS = 1500;
export const TOUCH_TIMEOUT_MS = 15000;
export const TOUCH_TIMEOUT_TEXT = 'Touchscreen did not respond';

export function touchscreenCheck(enabled: boolean | undefined, target: boolean, elapsedMs: number): 'done' | 'wait' | 'timeout' {
  if (enabled === target) return 'done';
  return elapsedMs >= TOUCH_TIMEOUT_MS ? 'timeout' : 'wait';
}

// ---------- support link ----------

export const SUPPORT_URL = 'https://ko-fi.com/musika';
export const SUPPORT_LABEL = 'Support Musika on Ko-fi';
